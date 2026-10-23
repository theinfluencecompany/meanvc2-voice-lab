"""Protocol tests: synthetic PCM + stub VC, not Google, real VC or RTA QA."""
import asyncio
import base64
import json
from pathlib import Path
import unittest
import numpy as np
from gemini_bridge import DEFAULT_QUEUE_BLOCKS,NativeAudioBridge,consume_provider_session,sdk_event
from native_playback import NativePlaybackBridge
from playback_format import PlaybackFormat


def event(pcm,complete=False):
    return {'serverContent':{'modelTurn':{'parts':[{'inlineData':{'mimeType':'audio/pcm;rate=24000',
        'data':base64.b64encode(pcm).decode()}}]},'turnComplete':complete}}


def sine(n):
    return (np.sin(np.arange(n)*2*np.pi*440/24000)*12000).astype('<i2').tobytes()


class StubVC:
    """Echoes input solely to observe incremental protocol/state handling."""
    def __init__(self):
        self.starts=[];self.chunks=[];self.cancels=[];self.flushes=[]
        self.block=None;self.entered=asyncio.Event();self.output_sequences={}
    async def start(self,token):
        self.starts.append(token)
        identity={'session_id':'stub','turn_id':token,'generation_id':str(len(self.starts))}
        self.output_sequences[token]=0;return identity
    def response(self,identity,pcm=b''):
        seq=self.output_sequences[identity['turn_id']]
        if pcm:self.output_sequences[identity['turn_id']]+=1
        return {**identity,'protocol_version':'meanvc2.local.v1','sample_rate':16000,'channels':1,
            'discarded':False,'sample_count':len(pcm)//2,'pcm_s16le_base64':base64.b64encode(pcm).decode(),'output_sequence':seq}
    async def chunk(self,identity,seq,pcm):
        self.chunks.append((identity,seq,pcm));self.entered.set()
        if self.block:await self.block.wait()
        return self.response(identity,pcm)
    async def flush(self,identity):self.flushes.append(identity);return self.response(identity)
    async def cancel(self,identity):self.cancels.append(identity);return {'discarded':True}
    async def close(self):pass


class Tests(unittest.IsolatedAsyncioTestCase):
    async def create_native(self, limit=DEFAULT_QUEUE_BLOCKS):
        audio=[];cancels=[];ended=[]
        bridge=NativePlaybackBridge(None,lambda token,pcm,meta:audio.append((token,pcm,meta)),
                                    cancels.append,limit,on_end=ended.append)
        bridge.start_worker();self.addAsyncCleanup(bridge.close)
        return bridge,audio,cancels,ended

    async def test_native_preserves_pcm_rate_order_and_turn_identity(self):
        bridge,audio,_,ended=await self.create_native()
        chunks=[sine(617),sine(523),sine(349)]
        for pcm in chunks:
            bridge.submit(event(pcm));await bridge.drain()
        self.assertEqual([packet[1] for packet in audio],chunks)
        self.assertEqual([packet[2]['output_sequence'] for packet in audio],[0,1,2])
        self.assertTrue(all(packet[2]['sample_rate']==24000 for packet in audio))
        self.assertEqual(len({packet[0] for packet in audio}),1)
        self.assertFalse(ended)  # Output is delivered before provider completion.
        bridge.submit({'serverContent':{'turnComplete':True}});await bridge.drain()
        self.assertEqual(ended,[audio[0][0]])

    async def test_native_local_interrupt_discards_queue_and_waits_for_provider_boundary(self):
        bridge,audio,cancels,ended=await self.create_native()
        bridge.submit(event(sine(6000)))
        old=bridge.current.token
        bridge.interrupt(local=True)
        bridge.interrupt(local=True)
        bridge.submit(event(sine(6000)))
        await bridge.drain()
        self.assertFalse(audio);self.assertFalse(ended);self.assertEqual(cancels,[old])
        bridge.submit(event(sine(6000),complete=True))  # Old turn's boundary is discarded too.
        await bridge.drain();self.assertFalse(audio)
        bridge.submit(event(sine(1000),complete=True));await bridge.drain()
        self.assertEqual(len(audio),1);self.assertNotEqual(audio[0][0],old)
        self.assertEqual(audio[0][2]['output_sequence'],0)

    async def test_native_interrupt_after_complete_does_not_swallow_next_reply(self):
        bridge,audio,cancels,_=await self.create_native()
        bridge.interrupt(local=True)  # Idle VAD before a reply must not suppress it.
        bridge.submit(event(sine(1000),complete=True));await bridge.drain()
        old=audio[0][0]
        bridge.interrupt(local=True)
        bridge.submit(event(sine(1000),complete=True));await bridge.drain()
        self.assertEqual(len(audio),2);self.assertNotEqual(audio[1][0],old)
        self.assertEqual(cancels,[old])

    async def test_native_provider_interrupt_drops_colocated_audio(self):
        bridge,audio,cancels,_=await self.create_native()
        bridge.submit(event(sine(1000)));await bridge.drain()
        message=event(sine(1000));message['serverContent']['interrupted']=True
        bridge.submit(message);await bridge.drain()
        self.assertEqual(len(audio),1);self.assertEqual(cancels,[audio[0][0]])
        bridge.submit(event(sine(1000),complete=True));await bridge.drain()
        self.assertEqual(len(audio),2)

    async def test_native_bounded_queue_fails_closed(self):
        bridge,audio,_,_=await self.create_native(limit=1)
        bridge.submit(event(sine(1000)))
        with self.assertRaisesRegex(RuntimeError,'backpressure'):
            bridge.submit(event(sine(1000)))
        await bridge.drain();self.assertFalse(audio)

    async def test_google_ready_fires_only_after_successful_provider_connect(self):
        from contextlib import asynccontextmanager
        from types import SimpleNamespace as NS
        from unittest.mock import patch
        import google_session
        for fails in [False,True]:
            events=[]
            @asynccontextmanager
            async def connect(**kwargs):
                events.append('connect-attempt')
                detection=kwargs['config']['realtime_input_config']['automatic_activity_detection']
                self.assertFalse(detection['disabled'])
                self.assertGreater(detection['silence_duration_ms'],0)
                self.assertEqual(kwargs['config']['speech_config']['voice_config']['prebuilt_voice_config']['voice_name'], 'Schedar')
                if fails:raise ConnectionError('private authenticated URL')
                events.append('connected')
                yield object()
            async def close():events.append('closed')
            async def consume(session,bridge,user_events,on_transcript):
                self.assertIsInstance(bridge,NativePlaybackBridge)
                events.append('consume')
            fake=NS(genai=NS(Client=lambda **kwargs:NS(aio=NS(live=NS(connect=connect),aclose=close))))
            with patch.dict('sys.modules',{'google':fake}),patch.object(google_session,'consume_provider_session',consume):
                call=google_session.run_google_live(api_key='unit-test-only',model='explicit-test-model',
                    persona_instructions='Test',user_events=None,vc_sink=None,on_audio=lambda *args:None,
                    on_cancel=lambda token:None,on_ready=lambda:events.append('ready'),
                    bridge_factory=NativePlaybackBridge)
                if fails:
                    with self.assertRaisesRegex(RuntimeError,'^Live session failed \\(ConnectionError\\); no raw provider details logged$'):
                        await call
                    self.assertEqual(events,['connect-attempt','closed'])
                else:
                    await call
                    self.assertEqual(events,['connect-attempt','connected','ready','consume','closed'])

    async def create(self,limit=DEFAULT_QUEUE_BLOCKS):
        sink=StubVC();audio=[];cancel=[]
        bridge=NativeAudioBridge(sink,lambda token,pcm,meta:audio.append((token,pcm,meta)),cancel.append,limit)
        bridge.start_worker();self.addAsyncCleanup(bridge.close)
        return sink,bridge,audio,cancel

    async def test_outputs_before_provider_turn_completes(self):
        sink,bridge,audio,_=await self.create()
        bridge.submit(event(sine(12000)))
        await bridge.drain()
        self.assertTrue(sink.chunks);self.assertTrue(audio);self.assertFalse(sink.flushes)
        bridge.submit({'serverContent':{'turnComplete':True}});await bridge.drain()
        self.assertEqual(len(sink.flushes),1)
        self.assertEqual(sum(len(c[2])//2 for c in sink.chunks),8000)

    async def test_irregular_provider_chunks_preserve_resampling_state(self):
        data=sine(24000)
        sink,a,_,_=await self.create()
        for start in range(0,len(data),7200):
            a.submit(event(data[start:start+7200]));await a.drain()
        a.submit({'serverContent':{'turnComplete':True}});await a.drain()
        expected=b''.join(row[2] for row in sink.chunks)
        other,b,_,_=await self.create()
        for start in range(0,len(data),614):
            b.submit(event(data[start:start+614]));await b.drain()
        b.submit({'serverContent':{'turnComplete':True}});await b.drain()
        actual=b''.join(row[2] for row in other.chunks)
        self.assertEqual(actual,expected);self.assertEqual(len(actual)//2,16000)

    async def test_all_parts_are_processed(self):
        sink,bridge,_,_=await self.create()
        parts=event(sine(6000))['serverContent']['modelTurn']['parts']
        bridge.submit({'serverContent':{'modelTurn':{'parts':[{'text':'transcript'},*parts,*parts]},'turnComplete':True}})
        await bridge.drain();self.assertEqual(sum(len(c[2])//2 for c in sink.chunks),8000)

    async def test_interrupt_discards_inflight_and_queued_audio(self):
        sink,bridge,audio,cancels=await self.create()
        sink.block=asyncio.Event()
        bridge.submit(event(sine(12000)));await sink.entered.wait()
        bridge.submit({'serverContent':{'interrupted':True}})
        await asyncio.sleep(0)
        self.assertTrue(sink.cancels);self.assertTrue(cancels)
        sink.block.set();await bridge.drain()
        self.assertFalse(audio);self.assertGreater(bridge.stats['discarded_results'],0)
        bridge.submit(event(sine(6000),complete=True));await bridge.drain()
        self.assertTrue(audio);self.assertEqual(len(sink.starts),2)

    async def test_local_barge_in_suppresses_until_provider_boundary(self):
        sink,bridge,_,_=await self.create()
        bridge.submit(event(sine(6000)));await bridge.drain()
        bridge.interrupt(local=True)
        before=bridge.stats['provider_pcm_parts']
        bridge.submit(event(sine(6000)))
        self.assertEqual(bridge.stats['provider_pcm_parts'],before)
        bridge.submit({'serverContent':{'interrupted':True}})
        bridge.submit(event(sine(6000),complete=True));await bridge.drain()
        self.assertEqual(len(sink.starts),2)

    async def test_bounded_queue_fails_closed(self):
        sink,bridge,audio,_=await self.create(limit=2)
        with self.assertRaisesRegex(RuntimeError,'backpressure'):bridge.submit(event(sine(24000)))
        await bridge.drain();self.assertFalse(audio)

    async def test_maximum_legal_provider_part_is_incremental_and_lossless(self):
        sink,bridge,audio,_=await self.create()
        bridge.submit(event(sine(96000),complete=True))
        await asyncio.wait_for(bridge.drain(),timeout=2)
        self.assertEqual(len(sink.starts),1);self.assertEqual(len(sink.flushes),1)
        self.assertEqual(len(sink.chunks),25)
        self.assertEqual([chunk[1] for chunk in sink.chunks],list(range(25)))
        self.assertEqual(sum(len(chunk[2])//2 for chunk in sink.chunks),64000)
        self.assertEqual(b''.join(chunk[2] for chunk in sink.chunks),b''.join(packet[1] for packet in audio))
        self.assertLessEqual(bridge.stats['max_queue_blocks'],DEFAULT_QUEUE_BLOCKS)
        self.assertTrue(bridge.current.complete)

    async def test_idle_local_interrupt_before_first_turn_does_not_swallow_reply(self):
        sink,bridge,audio,cancels=await self.create()
        bridge.interrupt(local=True);bridge.interrupt(local=True)
        bridge.submit(event(sine(6000),complete=True));await bridge.drain()
        self.assertEqual(len(sink.starts),1);self.assertTrue(audio);self.assertFalse(cancels)

    async def test_idle_local_interrupt_after_provider_cancel_does_not_swallow_next_reply(self):
        sink,bridge,audio,cancels=await self.create()
        bridge.submit(event(sine(6000)));await bridge.drain()
        bridge.submit({'serverContent':{'interrupted':True}})
        self.assertEqual(len(cancels),1)
        before=len(audio)
        bridge.interrupt(local=True)
        bridge.submit(event(sine(6000),complete=True));await bridge.drain()
        self.assertEqual(len(sink.starts),2);self.assertGreater(len(audio),before)

    async def test_live_consumer_propagates_cancel_failure_and_closes_both_input_tasks(self):
        from types import SimpleNamespace as NS
        events=[];waiting=asyncio.Event()
        class FailedCancelVC(StubVC):
            async def cancel(self,identity):
                self.cancels.append(identity)
                raise ConnectionError('private cancellation transport details')
            async def close(self):events.append('vc-closed')
        class Provider:
            async def receive(self):
                try:
                    yield NS(server_content=NS(model_turn=NS(parts=[NS(inline_data=NS(
                        data=sine(6000),mime_type='audio/pcm;rate=24000'))])))
                    await waiting.wait()
                finally:events.append('receiver-closed')
        sink=FailedCancelVC()
        bridge=NativeAudioBridge(sink,lambda *args:None,lambda token:events.append('playback-cleared'))
        async def user_events():
            try:
                await sink.entered.wait()
                yield {'kind':'barge_in'}
                await waiting.wait()
            finally:events.append('sender-closed')
        with self.assertRaisesRegex(RuntimeError,'^Conversion cancellation transport failed$'):
            await asyncio.wait_for(consume_provider_session(Provider(),bridge,user_events()),timeout=2)
        self.assertEqual(len(sink.cancels),1)
        self.assertCountEqual(events,['playback-cleared','receiver-closed','sender-closed','vc-closed'])
        self.assertTrue(bridge.worker.done())
        self.assertFalse(bridge.control_tasks)

    async def test_wrong_pcm_rate_rejected(self):
        _,bridge,_,_=await self.create()
        message=event(sine(6000));message['serverContent']['modelTurn']['parts'][0]['inlineData']['mimeType']='audio/pcm;rate=16000'
        with self.assertRaises(ValueError):bridge.submit(message)

    async def test_interrupted_message_does_not_play_colocated_parts(self):
        sink,bridge,audio,_=await self.create()
        message=event(sine(6000));message['serverContent']['interrupted']=True
        bridge.submit(message);await bridge.drain()
        self.assertFalse(sink.starts);self.assertFalse(audio)

    async def test_sdk_binary_blob_normalization(self):
        from types import SimpleNamespace as NS
        pcm=sine(6000)
        obj=NS(server_content=NS(interrupted=False,turn_complete=True,model_turn=NS(parts=[NS(inline_data=NS(data=pcm,mime_type='audio/pcm;rate=24000'))])))
        sink,bridge,_,_=await self.create();bridge.submit(sdk_event(obj));await bridge.drain()
        self.assertEqual(sum(len(row[2])//2 for row in sink.chunks),4000)

    async def test_output_resampler_declares_real_rate_and_flushes_once(self):
        output=[];clears=[]
        p=PlaybackFormat(48000,lambda token,pcm,meta:output.append((pcm,meta)),clears.append)
        source=sine(16000)
        for seq,pos in enumerate(range(0,len(source),5120)):p.audio('t',source[pos:pos+5120],{'sample_rate':16000,'channels':1,'output_sequence':seq})
        p.end('t');p.end('t')
        self.assertEqual(sum(len(x[0])//2 for x in output),48000)
        self.assertTrue(all(x[1]['sample_rate']==48000 for x in output))
        self.assertEqual([x[1]['sequence'] for x in output],list(range(len(output))))

    async def test_cancel_does_not_flush_output_resampler_tail(self):
        output=[];clears=[]
        p=PlaybackFormat(48000,lambda token,pcm,meta:output.append(pcm),clears.append)
        p.audio('t',sine(100),{'sample_rate':16000,'channels':1,'output_sequence':0})
        before=len(output);p.cancel('t');p.end('t')
        p.audio('t',sine(6000),{'sample_rate':16000,'channels':1,'output_sequence':1})
        self.assertEqual(len(output),before);self.assertEqual(clears,['t'])

    async def test_barge_in_after_provider_complete_still_clears_playback(self):
        sink,bridge,audio,cancels=await self.create()
        bridge.submit(event(sine(6000),complete=True));await bridge.drain()
        self.assertTrue(bridge.current.complete);self.assertTrue(audio)
        token=bridge.current.token
        bridge.interrupt(local=True)
        self.assertEqual(cancels,[token])
        self.assertFalse(bridge.suppress_until_boundary)
        bridge.submit(event(sine(6000),complete=True));await bridge.drain()
        self.assertEqual(len(sink.starts),2)
        self.assertNotEqual(audio[-1][0],token)

    async def test_barge_in_after_provider_boundary_during_conversion_keeps_next_reply(self):
        sink,bridge,audio,cancels=await self.create()
        sink.block=asyncio.Event()
        bridge.submit(event(sine(6000),complete=True));await sink.entered.wait()
        token=bridge.current.token
        self.assertFalse(bridge.current.complete)
        self.assertTrue(bridge.current.provider_complete)
        bridge.interrupt(local=True)
        self.assertFalse(bridge.suppress_until_boundary)
        bridge.submit(event(sine(6000),complete=True))
        sink.block.set();await bridge.drain()
        self.assertEqual(cancels,[token])
        self.assertEqual(len(sink.starts),2)
        self.assertTrue(audio)
        self.assertTrue(all(packet[0]!=token for packet in audio))

    async def test_vc_wrong_output_rate_fails_closed(self):
        sink,bridge,audio,_=await self.create()
        original=sink.response
        sink.response=lambda identity,pcm=b'':{**original(identity,pcm),'sample_rate':48000}
        bridge.submit(event(sine(6000),complete=True))
        with self.assertRaisesRegex(RuntimeError,'PCM format'):await bridge.drain()
        self.assertFalse(audio)

    async def test_duplicate_vc_output_sequence_fails_closed(self):
        sink,bridge,audio,_=await self.create()
        original=sink.response
        sink.response=lambda identity,pcm=b'':{**original(identity,pcm),'output_sequence':0}
        bridge.submit(event(sine(12000),complete=True))
        with self.assertRaisesRegex(RuntimeError,'sequence'):await bridge.drain()
        self.assertEqual(len(audio),1)

    async def test_missing_vc_output_sequence_fails_closed(self):
        sink,bridge,audio,_=await self.create()
        original=sink.response
        def missing(identity,pcm=b''):
            result=original(identity,pcm);result.pop('output_sequence');return result
        sink.response=missing
        bridge.submit(event(sine(6000),complete=True))
        with self.assertRaisesRegex(RuntimeError,'sequence'):await bridge.drain()
        self.assertFalse(audio)


if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(Tests)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    out=Path(__file__).resolve().parents[3]/'outputs/ivan-jp-pilot-20261020/live-source'
    out.mkdir(parents=True,exist_ok=True)
    (out/'protocol-checks.json').write_text(json.dumps({'passed':result.wasSuccessful(),'tests':result.testsRun,
        'scope':'synthetic PCM provider stub + echo VC stub; no Google API call, no real MeanVC2 or RTA output',
        'audio_source':'generated sine waveform, not prerecorded TTS'},indent=2)+'\n')
    raise SystemExit(not result.wasSuccessful())
