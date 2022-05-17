from flask import Flask, request, render_template
import asyncio
from datetime import datetime

import soundfile
import grpc
import requests
import io
from urllib.request import urlopen
import requests
# Speechly Identity API
from speechly.identity.v2.identity_api_pb2_grpc import IdentityAPIStub
from speechly.identity.v2.identity_api_pb2 import LoginRequest

# Speechly SLU API
from speechly.slu.v1.slu_pb2 import SLURequest, SLUConfig, SLUEvent
from speechly.slu.v1.slu_pb2_grpc import SLUStub

async def login(channel, device_id, app_id=None, project_id=None):
    assert device_id, 'UUID device_is required'
    assert (app_id or project_id), 'app_id or project_id is required'
    identity_api = IdentityAPIStub(channel)
    req = LoginRequest(device_id=device_id)
    if app_id:
        # if a token with a single app_id is required:
        req.application.app_id = app_id
    else:
        # get a token that is usable for all apps in project:
        req.project.project_id = project_id
    response = await identity_api.Login(req)
    token = response.token
    expires = datetime.fromisoformat(response.expires_at)
    return token, expires

async def stream_speech(channel, token, audio_stream, app_id=None):
    auth = ('authorization', f'Bearer {token}')

    async def read_responses(stream):
        transcript = []
        intent = ''
        entities = []
        resp = await stream.read()
        while resp != grpc.aio.EOF:
            if resp.HasField('started'):
                print(f'audioContext {resp.audio_context} started')
            elif resp.HasField('transcript'):
                w = resp.transcript
                transcript.append(w.word)
                print(w.word, w.start_time, w.end_time)
            elif resp.HasField('entity'):
                entities.append(resp.entity.entity)
            elif resp.HasField('intent'):
                intent = resp.intent.intent
            elif resp.HasField('finished'):
                print(f'audioContext {resp.audio_context} finished')
            resp = await stream.read()
        return intent, entities, transcript

    async def send_audio(stream, source):
        await stream.write(SLURequest(event=SLUEvent(event='START', app_id=app_id)))
        for chunk in source:
            await stream.write(SLURequest(audio=bytes(chunk)))
        await stream.write(SLURequest(event=SLUEvent(event='STOP')))
        await stream.done_writing()

    async with channel:
        slu = SLUStub(channel)
        try:
            stream = slu.Stream(metadata=[auth])
            config = SLUConfig(channels=1, sample_rate_hertz=16000)
            await stream.write(SLURequest(config=config))
            recv = read_responses(stream)
            send = send_audio(stream, audio_stream)
            r = await asyncio.gather(recv, send)
            intent, entities, transcript = r[0]
            print('Transcript:', ' '.join(transcript))
        except grpc.aio.AioRpcError as e:
            print('Error in SLU', str(e.code()), e.details())
    return transcript

async def main(url):
    #url = 'http://www.fit.vutbr.cz/~motlicek/sympatex/f2bjrop1.0.wav'
    #data = sf.blocks(io.BytesIO(urlopen(url).read()))
    #urlopen(url).read()
    response = requests.get(url)
    audio_blocks = soundfile.blocks(io.BytesIO(response.content), dtype='int16', blocksize=512)
    #audio_blocks = soundfile.blocks('test.wav', dtype='int16', blocksize=512)

    channel = grpc.aio.secure_channel(
        target=f'api.speechly.com:443',
        credentials=grpc.ssl_channel_credentials(),
        options=[('grpc.default_authority', f'api.speechly.com')]
    )

    # this is a randomly generated device id
    device_id = '9116b05b-d4a6-4073-b3b9-43dfb04c83ba'

    # login to get API token
    token, _ = await login(channel, device_id, app_id="9aa22c42-a06e-4ab3-ba3c-228904e2cf5a")

    # stream audio and print results in return
    transcript = await stream_speech(channel, token, audio_blocks, app_id="9aa22c42-a06e-4ab3-ba3c-228904e2cf5a")
    return transcript


app = Flask(__name__)

@app.route('/')
def my_form():
    return render_template('my-form.html')

@app.route('/', methods=['POST'])
def my_form_post():
    url = request.form['text']
    transcribed_results = asyncio.run(main(url))
    transcribed_results_string = " ".join(transcribed_results)
    result = {'TranscribedText':[transcribed_results_string]}
    return render_template('my-form-1.html',processed_text=result)

@app.route('/get-speechly', methods=['POST'])
def speechly_api():
    data = request.json
    url = data['url']
    transcribed_results = asyncio.run(main(url))
    transcribed_results_string = " ".join(transcribed_results)
    result = {'TranscribedText':[transcribed_results_string]}
    return result

if __name__ == '__main__':
    app.run(debug=True, use_reloader=True)
