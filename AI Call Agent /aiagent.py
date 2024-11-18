

import io
import base64
import json
import logging
import os
import asyncio
import subprocess
from fastapi import FastAPI, Response, WebSocket, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.websockets import WebSocketDisconnect
import requests
from twilio.twiml.voice_response import VoiceResponse, Connect
from twilio.rest import Client
from dotenv import load_dotenv
from pydantic import BaseModel
from ibm_watson import AssistantV2, SpeechToTextV1, DiscoveryV2
from ibm_cloud_sdk_core.authenticators import IAMAuthenticator
from ibm_watson.websocket import RecognizeCallback, AudioSource
import json
import os
from ibm_watson import DiscoveryV2
from ibm_watson.discovery_v2 import TrainingExample
from ibm_cloud_sdk_core.authenticators import CloudPakForDataAuthenticator, BearerTokenAuthenticator
from pydub import AudioSegment
# Load environment variables from .env file
load_dotenv()
logger = logging.getLogger(__name__)
audio_buffer = bytearray()  # Buffer to hold audio data

# Configuration
TWILIO_ACCOUNT_SID = os.getenv('TEST_TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TEST_TWILIO_AUTH_TOKEN')
TWILIO_PHONE_NUMBER = os.getenv('TEST_TWILIO_PHONE_NUMBER')
bearer_token = os.getenv('Bearer_Token')
PORT = int(os.getenv('PORT', 5050))

# Initialize Watson services
watson_api_key = os.getenv("WATSON_API_KEY")
watson_url = os.getenv("WATSON_URL")

# Initialize IBM Watson Assistant
# assistant_authenticator = IAMAuthenticator(watson_api_key)
# assistant = AssistantV2(
#     version='2023-04-01',
#     authenticator=assistant_authenticator
# )
# assistant.set_service_url(watson_url)
# Set up the authenticator
authenticator = IAMAuthenticator('p-zQHx10Sr-XwzZ_P13vrAcZ0wWnydjogQDxXVyjq_ES')
assistant = AssistantV2(
    version='2023-06-14',
    authenticator=authenticator
)

# Replace with your actual service URL
assistant.set_service_url('https://api.us-east.assistant.watson.cloud.ibm.com/')


# Initialize IBM Watson Speech to Text
speech_authenticator = IAMAuthenticator('uU61uw_J-KOFnP6P6LnXy5VaFBh2SU2pqiJFgheLETQm')
speech_to_text = SpeechToTextV1(authenticator=speech_authenticator)
speech_to_text.set_service_url('https://api.au-syd.speech-to-text.watson.cloud.ibm.com/instances/e5c8d9c4-33b5-425a-bf7d-8600c6866d32')
api_key = 'i36FwHJRMyGWkpY68ql20yqKwCSAwOCVVZm-u8FBmLRO'  # Replace with your actual API key
url = 'https://iam.cloud.ibm.com/identity/token'
headers = {
    'Content-Type': 'application/x-www-form-urlencoded'
}
data = {
    'apikey': api_key,
    'grant_type': 'urn:ibm:params:oauth:grant-type:apikey'
}

response = requests.post(url, headers=headers, data=data)
if response.status_code == 200:
    bearer_token = response.json()['access_token']
else:
    print("Error obtaining Bearer Token:", response.text)
## Option 2: bearer token
discovery_authenticator = BearerTokenAuthenticator(bearer_token)

## Initialize discovery instance ##
discovery = DiscoveryV2(version='2019-11-22', authenticator=discovery_authenticator)
discovery.set_service_url(
    'https://api.au-syd.discovery.watson.cloud.ibm.com'
)
discovery.set_disable_ssl_verification(False)

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Check required configurations
if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
    raise ValueError('Missing required configuration in the .env file.')

@app.get("/", response_class=JSONResponse)
async def index_page():
    return {"message": "Twilio Media Stream Server is running!"}

# Class for call initiation request
class CallRequest(BaseModel):
    to: str  # The destination phone number

@app.post("/initiate-call")
async def initiate_call(call_request: CallRequest):
    """Initiate a call to the specified phone number."""
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

    valid_test_numbers = [...]  # Add valid test numbers here

    if call_request.to not in valid_test_numbers:
        raise HTTPException(status_code=400, detail="Invalid 'to' number for testing.")

    try:
        call = client.calls.create(
            to=call_request.to,
            from_=TWILIO_PHONE_NUMBER,
            url='https://66aa-196-188-34-76.ngrok-free.app/incoming-call'  # Your public URL
        )
        return JSONResponse(content={"message": "Call initiated!", "call_sid": call.sid})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.api_route('/incoming-call', methods=['GET','POST'])  # Change to POST
async def incoming_call(request: Request):
    """Handle incoming calls and prompt for user speech input using <Gather>."""
    response = VoiceResponse()
    response.say("Welcome! Please tell me your needs after the beep.")
    
    # Use Gather to collect speech input; maxLength is the maximum length of speech input
    response.gather(input='speech', action='/handle-gather', speech_timeout='auto', max_length=60)

    return Response(content=str(response), media_type='application/xml')

@app.api_route("/handle-gather", methods=['POST'])
async def handle_gather(request: Request):
    """Process the user's speech input captured by <Gather>."""
    speech_result = (await request.form()).get("SpeechResult")  # Get the recognized speech
    print('speech_result: ',speech_result)
    
    if speech_result:
        # Process the speech input
        assistant_response = await process_with_assistant(speech_result)
        
        # Prepare and send response
        response = VoiceResponse()
        response.say(f"You said: {speech_result}. The assistant says: {assistant_response}")
        return Response(content=str(response), media_type='application/xml')
    else:
        # If no input was recognized, prompt again
        response = VoiceResponse()
        response.say("I did not receive any input. Please try again.")
        response.gather(input='speech', action='/handle-gather', speech_timeout='auto', max_length=60)
        return Response(content=str(response), media_type='application/xml')
    
async def process_with_assistant(transcription):
    """Process the transcription with IBM Watson Assistant."""
    session_response = assistant.create_session('52e82512-3e2f-4893-8694-0d4a0d69a271').get_result()
    print('session_response resnj......', session_response)
    session_id = session_response['session_id']
    
    response = assistant.message(
        '52e82512-3e2f-4893-8694-0d4a0d69a271',
        session_id,
        input={'text': transcription}
    ).get_result()
    
    # Check if 'output' and 'generic' exist and are not empty
    if 'output' in response and 'generic' in response['output'] and response['output']['generic']:
        return response['output']['generic'][0]['text']
    
    return "I'm sorry, but I couldn't process your request."
async def analyze_transcription_with_discovery(transcription):
    """Send the transcription to IBM Watson Discovery for analysis."""
    try:
        # Here you would implement the logic to send the transcription to Watson Discovery
        # For example, you might create a document in Discovery for analysis
        response = discovery.add_document(
            project_id='aa8d02f8-ef40-4507-a05b-d323d53e6ad0',
            collection_id='56297da0-1ca6-0ab9-0000-019324c63c39',
            file=json.dumps({"text": transcription}),
            filename='transcription.json'
        ).get_result()
        print(f"Discovery response: {response}")
        return response
    except Exception as e:
        logger.error(f"Error during analysis with Discovery: {e}")
        return None


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
