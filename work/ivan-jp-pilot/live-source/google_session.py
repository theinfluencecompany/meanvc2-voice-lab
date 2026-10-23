"""Network entry point; requires an explicitly supplied Google key and model.

The caller owns credentials and device audio. This adapter neither selects
another TTS nor reads WAVs; real Live checks are saved separately from protocol tests.
"""
import inspect
from gemini_bridge import NativeAudioBridge, consume_provider_session

GEMINI_VOICE_NAME = 'Schedar'
VOICE_DELIVERY = ('発声は標準的で明瞭な成人男性の普通の話し声にしてください。'
                  '自然な中程度の声量と速さで、息漏れ、ささやき、ASMR、過度なかすれ声や唸り声は避けてください。'
                  'キャラクターの性格にかかわらず、この発声方法を優先してください。')


async def run_google_live(*,api_key,model,persona_instructions,user_events,vc_sink,on_audio,on_cancel,
                          on_end=None,on_transcript=None,initial_context=None,on_ready=None,
                          bridge_factory=NativeAudioBridge,voice_name=GEMINI_VOICE_NAME,voice_delivery=None,language='ja'):
    if not api_key or not model or not persona_instructions:
        raise ValueError('Explicit API key, entitled Live model and server-authored character instructions are required')
    if voice_name not in {'Iapetus', 'Kore', 'Schedar'}:
        raise ValueError('Unsupported character source voice')
    if language not in {'ja', 'en'}:
        raise ValueError('Unsupported call language')
    language_rule = ('This call is in English. Speak natural English, including the first reply. '
                     'This overrides any default language mentioned in the character background. '
                     'Change language only when the user explicitly requests it.' if language == 'en' else
                     'この通話は自然な日本語で話してください。キャラクター背景の言語指定より、この通話の言語を優先してください。'
                     'ユーザーが明示的に別の言語を希望した場合は、その言語に切り替えてください。')
    try:
        from google import genai
    except ImportError:
        raise RuntimeError('google-genai SDK is not installed in this isolated runtime') from None
    config={'response_modalities':['AUDIO'],
            'system_instruction':persona_instructions+'\n'+(voice_delivery or VOICE_DELIVERY)+'\n'+language_rule,
            'speech_config':{'voice_config':{'prebuilt_voice_config':{'voice_name':voice_name}}},
            'input_audio_transcription':{},'output_audio_transcription':{},
            # Native microphone input needs a concrete turn boundary. The Live
            # audio probe succeeded with this configuration; an omitted VAD
            # configuration connected but produced no input/output events.
            'realtime_input_config':{'automatic_activity_detection':{
                'disabled':False,'silence_duration_ms':500,'prefix_padding_ms':20}}}
    # Native audio selects language from instructions/conversation; it does not
    # accept a hard languageCode. Delivery instructions request plain speech;
    # they do not guarantee the complete absence of breathiness.
    client=genai.Client(api_key=api_key,http_options={'api_version':'v1beta'})
    bridge=bridge_factory(vc_sink,on_audio,on_cancel,on_end=on_end)
    try:
        async with client.aio.live.connect(model=model,config=config) as session:
            if initial_context:await session.send_client_content(turns=initial_context,turn_complete=False)
            if on_ready:
                ready=on_ready()
                if inspect.isawaitable(ready):await ready
            await consume_provider_session(session,bridge,user_events,on_transcript)
    except Exception as exc:
        # A provider exception can embed its authenticated URL. Never expose it.
        raise RuntimeError(f'Live session failed ({type(exc).__name__}); no raw provider details logged') from None
    finally:
        try:await client.aio.aclose()
        except Exception:pass  # Never print an authenticated transport URL on teardown.
