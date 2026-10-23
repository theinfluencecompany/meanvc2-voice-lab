"""Incremental Gemini native PCM -> MeanVC2 transport.

No prerecorded WAV source, microphone UI, avatar renderer or production routing
is supplied here. Inject real provider/session and output callbacks to integrate.
"""
from __future__ import annotations
import asyncio
import base64
from dataclasses import dataclass, field
import json
import uuid
import urllib.request
import numpy as np
import soxr

MAX_PROVIDER_PCM_BYTES = 4 * 24000 * 2
VC_BLOCK_BYTES = 2560 * 2
# A legal four-second provider part yields 25 conversion blocks. Reserve start,
# end and one carried partial block as well: submit() cannot yield to the worker
# halfway through a provider event. This bounds a burst; it is no capacity claim.
DEFAULT_QUEUE_BLOCKS = 28


@dataclass
class Turn:
    token: str = field(default_factory=lambda: uuid.uuid4().hex)
    resampler: object = field(default_factory=lambda: soxr.ResampleStream(24000,16000,1,dtype='float32',quality='HQ'))
    pending: bytes = b''
    identity: dict | None = None
    sequence: int = 0
    expected_output_sequence: int = 0
    cancelled: bool = False
    provider_complete: bool = False
    complete: bool = False
    cancel_sent: bool = False
    input_samples: int = 0
    output_samples: int = 0


class HttpVCSink:
    """Only the prepared local MeanVC2 protocol; not an RTA transport."""
    def __init__(self, url, session_id, profile, binding, processing_mode=None):
        self.url, self.session_id, self.profile, self.binding=url.rstrip('/'),session_id,profile,binding
        if processing_mode not in (None, 'prefix_quality'):
            raise ValueError('Unsupported local processing mode')
        self.processing_mode = processing_mode
        self.input_sample_format = 'float32le' if processing_mode == 'prefix_quality' else 's16le'
    async def request(self,path,body):
        def call():
            req=urllib.request.Request(self.url+path,json.dumps(body).encode(),{'Content-Type':'application/json'})
            with urllib.request.urlopen(req,timeout=15) as response:return json.load(response)
        return await asyncio.to_thread(call)
    async def start(self,token):
        selection = ({'processing_mode': self.processing_mode, 'input_sample_format': self.input_sample_format}
                     if self.processing_mode else {})
        result=await self.request('/v1/start',{'protocol_version':'meanvc2.local.v1',
            'session_id':self.session_id,'turn_id':token,'conversion_profile_id':self.profile,**self.binding,**selection})
        if self.processing_mode and result.get('processing_mode') != self.processing_mode:
            # Never present the old algorithm as the explicitly selected experiment.
            await self.close()
            raise RuntimeError('Voice service did not confirm the selected processing mode')
        if self.processing_mode and result.get('input_sample_format') != self.input_sample_format:
            await self.close()
            raise RuntimeError('Voice service did not confirm the selected input sample format')
        for key,value in self.binding.items():
            if result.get(key)!=value:raise RuntimeError('Voice profile binding changed')
        if (result.get('protocol_version')!='meanvc2.local.v1' or result.get('sample_rate')!=16000
            or result.get('channels')!=1 or result.get('conversion_profile_id')!=self.profile
            or result.get('session_id')!=self.session_id or result.get('turn_id')!=token
            or not isinstance(result.get('generation_id'),str) or not result['generation_id']):
            raise RuntimeError('Voice start response violates the prepared stream contract')
        return {k:result[k] for k in ['session_id','turn_id','generation_id']}
    async def chunk(self,identity,sequence,pcm):
        if self.input_sample_format == 'float32le':
            return await self.request('/v1/chunk',{**identity,'sequence':sequence,'sample_rate':16000,
                'channels':1,'sample_format':'float32le','pcm_f32le_base64':base64.b64encode(pcm).decode()})
        return await self.request('/v1/chunk',{**identity,'sequence':sequence,'sample_rate':16000,
            'channels':1,'pcm_s16le_base64':base64.b64encode(pcm).decode()})
    async def flush(self,identity):return await self.request('/v1/flush',identity)
    async def cancel(self,identity):return await self.request('/v1/cancel',identity)
    async def close(self):return await self.request('/v1/close',{'session_id':self.session_id})


class NativeAudioBridge:
    """Receiver never waits for voice inference; cancellation bypasses its queue.

    Provider WebSocket order is retained. A local barge-in suppresses old model
    audio until the provider acknowledges interruption/turn completion. Callers
    must also discard already-queued playback for the returned local turn token.
    """
    def __init__(self,sink,on_audio,on_cancel,queue_blocks=DEFAULT_QUEUE_BLOCKS,on_end=None):
        self.sink,self.on_audio,self.on_cancel=sink,on_audio,on_cancel
        self.float_input = getattr(sink, 'input_sample_format', 's16le') == 'float32le'
        self.vc_block_bytes = VC_BLOCK_BYTES * (2 if self.float_input else 1)
        self.on_end=on_end or (lambda token:None)
        self.queue=asyncio.Queue(maxsize=queue_blocks)
        self.producer=None
        self.current=None
        self.suppress_until_boundary=False
        self.control_tasks=set()
        self.control_error=None
        self.control_failed=asyncio.Event()
        self.worker=None
        self.stats={'provider_pcm_parts':0,'input_24k_samples':0,'resampled_16k_samples':0,
                    'vc_packets':0,'discarded_results':0,'interruptions':0,'max_queue_blocks':0}

    def start_worker(self):
        if self.worker is not None:raise RuntimeError('Worker already started')
        self.worker=asyncio.create_task(self._work())

    def _enqueue(self,turn,kind,data=None):
        try:self.queue.put_nowait((turn,kind,data))
        except asyncio.QueueFull:
            self.interrupt(local=True)
            raise RuntimeError('Live conversion backpressure: bounded audio queue exceeded')
        self.stats['max_queue_blocks']=max(self.stats['max_queue_blocks'],self.queue.qsize())

    def _task(self,coro):
        task=asyncio.create_task(coro);self.control_tasks.add(task)
        def finished(done):
            self.control_tasks.discard(done)
            if not done.cancelled() and done.exception() is not None:
                self.control_error=done.exception();self.control_failed.set()
        task.add_done_callback(finished)

    async def wait_control_failure(self):
        # The live receive/send loop does not call drain(). Wake that owner as
        # soon as an out-of-band cancellation fails, without exposing its URL.
        await self.control_failed.wait()
        raise RuntimeError('Conversion cancellation transport failed') from None

    def interrupt(self,local=False):
        turn=self.current
        pending_boundary=self.suppress_until_boundary or bool(self.producer and not self.producer.provider_complete)
        # Provider/model completion is not playback completion: a user can
        # interrupt PCM that is already queued after the provider has finished.
        if turn and not turn.cancelled:
            turn.cancelled=True;turn.pending=b''
            self.stats['interruptions']+=1
            self.on_cancel(turn.token)
            if turn.identity and not turn.cancel_sent and not turn.complete:
                turn.cancel_sent=True;self._task(self.sink.cancel(turn.identity))
        self.producer=None
        # A completed provider turn has already delivered its boundary. Waiting
        # for another one here would suppress the next response in full even
        # though only the old playback/inference tail was interrupted.
        # Idle VAD/barge-in events have no provider generation to suppress. Also
        # preserve an outstanding local cancellation until its actual boundary.
        self.suppress_until_boundary=local and pending_boundary

    def _new_turn(self):
        if self.current and not self.current.cancelled:self.interrupt()
        turn=Turn();self.producer=turn;self.current=turn
        self._enqueue(turn,'start')
        return turn

    def _emit_resampled(self,turn,data,last=False):
        # Match the accepted official-source normalization and retain the HQ
        # resampler's floats for the new mode. Requantizing this intermediate
        # waveform changes low-energy fbank features; legacy wire stays PCM16.
        samples=np.frombuffer(data,dtype='<i2').astype(np.float32)/(32767 if self.float_input else 32768)
        turn.input_samples+=len(samples)
        result=turn.resampler.resample_chunk(samples,last=last)
        turn.output_samples+=len(result)
        self.stats['input_24k_samples']+=len(samples)
        self.stats['resampled_16k_samples']+=len(result)
        turn.pending+=(result.astype('<f4').tobytes() if self.float_input else
                       np.clip(np.rint(result*32768),-32768,32767).astype('<i2').tobytes())
        while len(turn.pending)>=self.vc_block_bytes:
            self._enqueue(turn,'audio',turn.pending[:self.vc_block_bytes]);turn.pending=turn.pending[self.vc_block_bytes:]
        if last and turn.pending:
            self._enqueue(turn,'audio',turn.pending);turn.pending=b''

    def submit(self,message):
        """Process one genuine Gemini wire event; all audio parts are consumed."""
        if not isinstance(message,dict):raise ValueError('Expected provider object')
        content=message.get('serverContent')
        if content is None:return
        if not isinstance(content,dict):raise ValueError('Invalid serverContent')
        if content.get('interrupted'):
            self.interrupt(local=False)
            return  # Do not play co-located old audio in an interruption event.
        if self.suppress_until_boundary:
            if content.get('turnComplete'):self.suppress_until_boundary=False
            return
        parts=content.get('modelTurn',{}).get('parts',[])
        if not isinstance(parts,list):raise ValueError('Invalid model parts')
        for part in parts:
            inline=part.get('inlineData')
            if inline is None:continue
            if inline.get('mimeType')!='audio/pcm;rate=24000':
                raise ValueError('Expected Gemini native 24kHz mono PCM; do not infer rate from bytes')
            try:data=base64.b64decode(inline.get('data',''),validate=True)
            except (TypeError,ValueError):raise ValueError('Invalid provider PCM encoding')
            if len(data)%2 or len(data)>MAX_PROVIDER_PCM_BYTES:raise ValueError('Invalid/oversized provider PCM part')
            if not data:continue
            turn=self.producer or self._new_turn()
            self.stats['provider_pcm_parts']+=1
            self._emit_resampled(turn,data)
        if content.get('turnComplete') and self.producer:
            turn=self.producer
            turn.provider_complete=True
            self._emit_resampled(turn,b'',last=True)
            self._enqueue(turn,'end');self.producer=None

    async def _deliver(self,turn,result):
        if turn.cancelled or self.current is not turn or result.get('discarded'):
            self.stats['discarded_results']+=1;return
        for key,value in turn.identity.items():
            if result.get(key)!=value:raise RuntimeError('Stale/misbound VC response')
        if (result.get('protocol_version')!='meanvc2.local.v1' or result.get('sample_rate')!=16000
                or result.get('channels')!=1):
            raise RuntimeError('Unexpected VC output protocol or PCM format')
        sequence=result.get('output_sequence')
        if type(sequence) is not int or sequence!=turn.expected_output_sequence:
            raise RuntimeError('VC output sequence is missing, repeated or out of order')
        raw=base64.b64decode(result.get('pcm_s16le_base64',''),validate=True)
        if type(result.get('sample_count')) is not int or len(raw)!=result['sample_count']*2:
            raise RuntimeError('PCM length mismatch')
        if raw:
            turn.expected_output_sequence+=1
            self.on_audio(turn.token,raw,{'sample_rate':16000,'channels':1,
                'generation_id':turn.identity['generation_id'],'output_sequence':result['output_sequence']})

    async def _work(self):
        while True:
            turn,kind,data=await self.queue.get()
            try:
                if kind=='stop':return
                if turn.cancelled:continue
                if kind=='start':
                    turn.identity=await self.sink.start(turn.token)
                    if turn.cancelled and not turn.cancel_sent:
                        turn.cancel_sent=True;await self.sink.cancel(turn.identity)
                elif kind=='audio':
                    result=await self.sink.chunk(turn.identity,turn.sequence,data)
                    turn.sequence+=1;self.stats['vc_packets']+=1
                    await self._deliver(turn,result)
                elif kind=='end':
                    result=await self.sink.flush(turn.identity)
                    await self._deliver(turn,result)
                    if not turn.cancelled and self.current is turn:self.on_end(turn.token)
                    turn.complete=True
            except Exception:
                self.interrupt(local=True)
                raise
            finally:self.queue.task_done()

    async def drain(self):
        if self.worker is None:raise RuntimeError('Worker not started')
        joined=asyncio.create_task(self.queue.join())
        done,_=await asyncio.wait([joined,self.worker],return_when=asyncio.FIRST_COMPLETED)
        if self.worker in done:
            joined.cancel();await self.worker
        else:await joined
        if self.control_tasks:await asyncio.gather(*list(self.control_tasks))
        if self.control_error is not None:raise RuntimeError('Conversion cancellation transport failed') from None

    async def close(self):
        self.interrupt(local=True)
        if self.worker and not self.worker.done():
            await self.queue.put((None,'stop',None));await self.worker
        if self.control_tasks:await asyncio.gather(*list(self.control_tasks),return_exceptions=True)
        await self.sink.close()


def sdk_event(response):
    """Normalize google-genai object without assuming its bytes JSON serializer."""
    content=getattr(response,'server_content',None)
    if content is None:return {}
    result={'interrupted':bool(getattr(content,'interrupted',False)),
            'turnComplete':bool(getattr(content,'turn_complete',False))}
    turn=getattr(content,'model_turn',None)
    if turn:
        parts=[]
        for part in turn.parts or []:
            blob=getattr(part,'inline_data',None)
            if blob:
                parts.append({'inlineData':{'mimeType':blob.mime_type,'data':base64.b64encode(blob.data).decode()}})
        result['modelTurn']={'parts':parts}
    return {'serverContent':result}


async def consume_provider_session(session,bridge,user_events,on_transcript=None):
    """Input comes from actual client mic/camera/text events, never a WAV file."""
    bridge.start_worker()
    async def receive():
        while True:
            count=0
            async for response in session.receive():
                count+=1;bridge.submit(sdk_event(response))
                if on_transcript:
                    content=getattr(response,'server_content',None)
                    for role,field in [('user','input_transcription'),('model','output_transcription')]:
                        transcript=getattr(content,field,None) if content else None
                        if transcript and getattr(transcript,'text',None):on_transcript(role,transcript.text)
            if not count:raise ConnectionError('Provider session ended')
    async def send():
        async for event in user_events:
            kind=event['kind']
            if kind=='audio':
                if event.get('sample_rate')!=16000 or event.get('channels')!=1 or len(event['pcm_s16le'])%2:
                    raise ValueError('Client microphone audio must explicitly be 16kHz mono int16 PCM')
                await session.send_realtime_input(audio={'data':event['pcm_s16le'],'mime_type':'audio/pcm;rate=16000'})
            elif kind=='video':
                await session.send_realtime_input(video={'data':event['jpeg'],'mime_type':'image/jpeg'})
            elif kind=='text':await session.send_realtime_input(text=event['text'])
            elif kind=='barge_in':bridge.interrupt(local=True)
            elif kind=='audio_stream_end':await session.send_realtime_input(audio_stream_end=True)
            else:raise ValueError('Unknown client media event')
    receiver,sender=asyncio.create_task(receive()),asyncio.create_task(send())
    control_monitor=asyncio.create_task(bridge.wait_control_failure())
    try:
        # End of user_events means the client closed the conversation; it is
        # deliberately not a one-shot prompt list that waits for a final WAV.
        done,_=await asyncio.wait([receiver,sender,bridge.worker,control_monitor],return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if task.exception():raise task.exception()
    finally:
        receiver.cancel();sender.cancel();control_monitor.cancel()
        await asyncio.gather(receiver,sender,control_monitor,return_exceptions=True)
        await bridge.close()
