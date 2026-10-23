"""Explicit streaming 16kHz VC output -> downstream's declared rate.

This is a format adapter, not a player or avatar implementation. Both intended
consumers must receive the same canonical audio and use its sample timeline.
"""
import numpy as np
import soxr


class PlaybackFormat:
    def __init__(self,target_rate,on_pcm,on_clear):
        if target_rate not in (16000,24000,48000):raise ValueError('Declare the verified downstream rate explicitly')
        self.target_rate,self.on_pcm,self.on_clear=target_rate,on_pcm,on_clear
        self.states={};self.closed=[]
    def _output(self,token,samples,state):
        pcm=np.clip(np.rint(samples*32768),-32768,32767).astype('<i2').tobytes()
        if pcm:
            self.on_pcm(token,pcm,{'sample_rate':self.target_rate,'channels':1,
                'sequence':state['sequence'],'start_sample':state['samples']})
            state['sequence']+=1;state['samples']+=len(samples)
    def audio(self,token,pcm,metadata):
        if token in self.closed:return
        if metadata.get('sample_rate')!=16000 or metadata.get('channels')!=1 or len(pcm)%2:
            raise ValueError('Expected canonical converted 16kHz mono int16 PCM')
        state=self.states.get(token)
        if state is None:
            state={'resampler':None if self.target_rate==16000 else soxr.ResampleStream(16000,self.target_rate,1,dtype='float32',quality='HQ'),
                   'sequence':0,'samples':0,'source_sequence':0};self.states[token]=state
        if type(metadata.get('output_sequence')) is not int or metadata['output_sequence']!=state['source_sequence']:
            raise ValueError('Canonical PCM packet sequence is missing or out of order')
        state['source_sequence']+=1
        samples=np.frombuffer(pcm,dtype='<i2').astype(np.float32)/32768
        output=samples if state['resampler'] is None else state['resampler'].resample_chunk(samples)
        self._output(token,output,state)
    def _close(self,token):
        self.states.pop(token,None);self.closed.append(token);self.closed=self.closed[-128:]
    def end(self,token):
        state=self.states.get(token)
        if state and state['resampler'] is not None:
            self._output(token,state['resampler'].resample_chunk(np.empty(0,dtype=np.float32),last=True),state)
        self._close(token)
    def cancel(self,token):
        # Do not flush a cancelled resampler: its tail belongs to old speech.
        self._close(token);self.on_clear(token)
