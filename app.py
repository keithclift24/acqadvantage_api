import requests
import os
from flask import Flask, jsonify, request, Response, stream_with_context # Ensure stream_with_context is imported
from flask_socketio import SocketIO, emit
import openai
import json
import time 
import stripe
from dotenv import load_dotenv
from flask_cors import CORS

# Load environment variables from .env file 
load_dotenv()

# --- INITIALIZE SERVICES ---
app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*") 
openai_client = openai.OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')


# --- CORE LOGIC FUNCTIONS ---
def run_assistant_and_emit(data):
    """
    This function runs in a background thread. It handles the polling and
    emits the final result back to the client via WebSockets.
    """
    thread_id = data.get('thread_id')
    prompt = data.get('prompt')
    client_sid = data.get('sid') # The specific client to send the response to

    try:
        openai_client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=prompt
        )
        run = openai_client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id='asst_QUel0QQc2NvKSYZMBCgtStMb'
        )

        while run.status in ['queued', 'in_progress']:
            time.sleep(2)
            run = openai_client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)

        if run.status == 'completed':
            messages = openai_client.beta.threads.messages.list(thread_id=thread_id)
            assistant_message_content = messages.data[0].content[0].text.value
            
            try:
                # Robust JSON extraction
                start_index = assistant_message_content.find('{')
                end_index = assistant_message_content.rfind('}') + 1
                if start_index != -1 and end_index != -1:
                    json_string = assistant_message_content[start_index:end_index]
                    response_data = json.loads(json_string)
                    # Emit the final data to the specific client
                    socketio.emit('assistant_response', {'status': 'completed', 'response': response_data}, to=client_sid)
                else:
                    raise ValueError("No JSON object found")
            except (ValueError, json.JSONDecodeError):
                socketio.emit('assistant_response', {'status': 'failed', 'error': 'Failed to parse structured response.'}, to=client_sid)
        else:
            socketio.emit('assistant_response', {'status': 'failed', 'error': f'Run failed with status: {run.status}'}, to=client_sid)

    except Exception as e:
        print(f"Error in background task: {e}")
        socketio.emit('assistant_response', {'status': 'failed', 'error': str(e)}, to=client_sid)


def get_or_create_thread(user_token, user_object_id):
    base_url = "https://toughquilt.backendless.app/api"
    headers = {'user-token': user_token, 'Content-Type': 'application/json'}
    try:
        user_url = f"{base_url}/data/Users/{user_object_id}"
        user_response = requests.get(user_url, headers=headers)
        user_response.raise_for_status()
        user_data = user_response.json()
        if 'currentThreadId' in user_data and user_data['currentThreadId']:
            return user_data['currentThreadId']
        thread = openai_client.beta.threads.create()
        new_thread_id = thread.id
        update_payload = {'currentThreadId': new_thread_id}
        update_response = requests.put(user_url, json=update_payload, headers=headers)
        update_response.raise_for_status()
        return new_thread_id
    except Exception as e:
        print(f"An unexpected error occurred in get_or_create_thread: {e}")
        return None


def reset_user_thread(user_token, user_object_id):
    base_url = "https://toughquilt.backendless.app/api"
    headers = {'user-token': user_token, 'Content-Type': 'application/json'}
    try:
        user_url = f"{base_url}/data/Users/{user_object_id}"
        user_response = requests.get(user_url, headers=headers)
        user_response.raise_for_status()
        user_data = user_response.json()
        current_thread_id = user_data.get('currentThreadId')
        if current_thread_id:
            openai_client.beta.threads.delete(thread_id=current_thread_id)
        update_payload = {'currentThreadId': None}
        update_response = requests.put(user_url, json=update_payload, headers=headers)
        update_response.raise_for_status()
        return True
    except Exception as e:
        print(f"Error in reset_user_thread: {e}")
        return False


# --- WEBSOCKET EVENT HANDLERS ---
@socketio.on('connect')
def handle_connect():
    print(f"Client connected: {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    print(f"Client disconnected: {request.sid}")

@socketio.on('send_message')
def handle_send_message(data):
    """
    Handles incoming messages from the client via WebSocket.
    """
    print(f"Received message from {request.sid}: {data}")
    # Add the client's session ID to the data for the response
    data['sid'] = request.sid
    
    # --- PRESERVED USAGE LIMIT LOGIC ---
    user_token = data.get('user_token')
    object_id = data.get('objectId')

    if not user_token or not object_id:
        emit('assistant_response', {'status': 'failed', 'error': 'user_token and objectId are required.'})
        return

    base_url = "https://toughquilt.backendless.app/api"
    headers = {'user-token': user_token, 'Content-Type': 'application/json'}
    try:
        user_url = f"{base_url}/data/Users/{object_id}"
        user_response = requests.get(user_url, headers=headers)
        user_response.raise_for_status()
        user_data = user_response.json()
        daily_count = user_data.get('dailyQuestionCount', 0)
        if daily_count >= 100:
            emit('assistant_response', {'status': 'failed', 'error': 'Daily limit reached'})
            return
        new_count = daily_count + 1
        update_payload = {'dailyQuestionCount': new_count}
        update_response = requests.put(user_url, json=update_payload, headers=headers)
        update_response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Error validating user: {e}")
        emit('assistant_response', {'status': 'failed', 'error': 'Failed to validate user.'})
        return
    # --- END OF PRESERVED LOGIC ---

    # Start the AI processing in a background thread
    socketio.start_background_task(run_assistant_and_emit, data)
    # Optionally, send an immediate acknowledgment
    emit('message_received', {'status': 'ok', 'message': 'Processing started...'})


# --- API ENDPOINTS ---
@app.route('/')
def health_check():
    return jsonify({'status': 'API is running'})


@app.route('/start_chat', methods=['POST'])
def start_chat():
    user_token = request.headers.get('user-token')
    if not user_token:
        return jsonify({'error': 'User token is missing'}), 401
    data = request.get_json()
    if not data or 'objectId' not in data:
        return jsonify({'error': 'objectId is missing from request body'}), 400
    user_object_id = data['objectId']
    thread_id = get_or_create_thread(user_token, user_object_id)
    if thread_id:
        return jsonify({'thread_id': thread_id})
    else:
        return jsonify({'error': 'Failed to process request'}), 500


@app.route('/reset_thread', methods=['POST'])
def reset_thread():
    user_token = request.headers.get('user-token')
    if not user_token:
        return jsonify({'error': 'User token is missing'}), 401
    data = request.get_json()
    if not data or 'objectId' not in data:
        return jsonify({'error': 'objectId is missing from request body'}), 400
    user_object_id = data['objectId']
    success = reset_user_thread(user_token, user_object_id)
    if success:
        return jsonify({'status': 'success', 'message': 'Thread reset successfully'})
    else:
        return jsonify({'status': 'failure', 'message': 'Failed to reset thread'}), 500


@app.route('/create-checkout-session', methods=['POST'])
def create_checkout_session():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Request body is missing'}), 400
    
    plan_type = data.get('planType')
    user_object_id = data.get('objectId')
    
    if not plan_type or not user_object_id:
        return jsonify({'error': 'planType and objectId are required'}), 400

    price_ids = {
        'monthly': 'price_1Rl2mc2Lfw5u3Q4QuJGFFgiG',
        'annual': 'price_1Rl2pB2Lfw5u3Q4QFpW9Olha'
    }

    if plan_type not in price_ids:
        return jsonify({'error': 'Invalid plan_type'}), 400

    try:
        session = stripe.checkout.Session.create(
            mode='subscription',
            success_url='https://acqadvantage.com/?payment=success',
            cancel_url='https://acqadvantage.com/?page=home',
            client_reference_id=user_object_id,
            line_items=[{'price': price_ids[plan_type], 'quantity': 1}]
        )
        return jsonify({'checkout_url': session.url})
    except Exception as e:
        print(f"Error creating Stripe session: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/verify-payment-session', methods=['POST'])
def verify_payment_session():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Request body is missing'}), 400
    
    session_id = data.get('session_id')
    if not session_id:
        return jsonify({'error': 'session_id is required'}), 400

    try:
        session = stripe.checkout.Session.retrieve(session_id)
        
        if session.status != 'complete' or session.payment_status != 'paid':
            return jsonify({'error': 'Payment not successful'}), 400
        
        client_reference_id = session.client_reference_id
        subscription_id = session.subscription
        
        if not client_reference_id or not subscription_id:
            return jsonify({'error': 'Missing client_reference_id or subscription_id in session'}), 400
        
        base_url = "https://toughquilt.backendless.app/api"
        query_url = f"{base_url}/data/Subscriptions"
        query_params = {'where': f"ownerId.objectId = '{client_reference_id}'"}
        
        query_response = requests.get(query_url, params=query_params)
        query_response.raise_for_status()
        subscriptions = query_response.json()
        
        if not subscriptions:
            return jsonify({'error': f'No subscription found for user {client_reference_id}'}), 404
        
        subscription_object_id = subscriptions[0]['objectId']
        update_url = f"{base_url}/data/Subscriptions/{subscription_object_id}"
        update_payload = {'status': 'active', 'stripeSubscriptionId': subscription_id}
        
        update_response = requests.put(update_url, json=update_payload)
        update_response.raise_for_status()
        
        return jsonify({'status': 'success'}), 200
        
    except Exception as e:
        return jsonify({'error': f'Server error: {str(e)}'}), 500


@app.route('/stripe-webhook', methods=['POST'])
def stripe_webhook():
    payload = request.get_data()
    sig_header = request.headers.get('Stripe-Signature')
    
    if not sig_header:
        return jsonify({'error': 'Missing Stripe-Signature header'}), 400

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, os.getenv('STRIPE_WEBHOOK_SECRET')
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 400

    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        client_reference_id = session.get('client_reference_id')
        subscription_id = session.get('subscription')
        
        if not client_reference_id or not subscription_id:
            return jsonify({'status': 'success'}), 200
        
        base_url = "https://toughquilt.backendless.app/api"
        query_url = f"{base_url}/data/Subscriptions"
        query_params = {'where': f"ownerId.objectId = '{client_reference_id}'"}
        
        try:
            query_response = requests.get(query_url, params=query_params)
            query_response.raise_for_status()
            subscriptions = query_response.json()
            
            if not subscriptions:
                return jsonify({'status': 'success'}), 200
            
            subscription_object_id = subscriptions[0]['objectId']
            update_url = f"{base_url}/data/Subscriptions/{subscription_object_id}"
            update_payload = {'status': 'active', 'stripeSubscriptionId': subscription_id}
            
            update_response = requests.put(update_url, json=update_payload)
            update_response.raise_for_status()
            
        except Exception as e:
            print(f"Error in webhook updating subscription: {e}")
            return jsonify({'status': 'success'}), 200
    
    return jsonify({'status': 'success'}), 200


# --- MAIN EXECUTION ---
if __name__ == '__main__':
    # Use socketio.run to start the server
    socketio.run(app, debug=True)
