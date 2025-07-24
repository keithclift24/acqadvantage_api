import httpx
import os
from flask import Flask, jsonify, request, Response, stream_with_context # Ensure stream_with_context is imported
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
openai_client = openai.OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')


# --- CORE LOGIC FUNCTIONS ---
def generate_structured_response(thread_id, user_prompt):
    """
    Generator function that polls the assistant run and yields the final JSON payload.
    It sends a whitespace "heartbeat" every second to prevent client-side timeouts.
    """
    try:
        openai_client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=user_prompt
        )
        
        run = openai_client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id='asst_QUel0QQc2NvKSYZMBCgtStMb' # Your Assistant ID
        )

        # Poll for the run to complete, sending a heartbeat
        while run.status in ['queued', 'in_progress', 'cancelling']:
            time.sleep(1) 
            run = openai_client.beta.threads.runs.retrieve(
                thread_id=thread_id,
                run_id=run.id
            )
            # --- HEARTBEAT ---
            # Yield a single space to keep the connection alive.
            yield ' ' 

        if run.status == 'completed':
            messages = openai_client.beta.threads.messages.list(thread_id=thread_id)
            assistant_message_content = messages.data[0].content[0].text.value
            
            try:
                start_index = assistant_message_content.index('{')
                end_index = assistant_message_content.rindex('}') + 1
                json_string = assistant_message_content[start_index:end_index]
                yield json_string # Yield the final, complete JSON string
            except ValueError:
                print("Error: Could not find a valid JSON object in the assistant's response.")
                yield json.dumps({"error": "Failed to extract valid JSON from response."})
        else:
            print(f"Run failed with status: {run.status}")
            yield json.dumps({"error": f"Run failed with status: {run.status}"})

    except Exception as e:
        print(f"Error in generate_structured_response: {e}")
        yield json.dumps({"error": f"An error occurred: {str(e)}"})


def get_or_create_thread(user_token, user_object_id):
    base_url = "https://toughquilt.backendless.app/api"
    headers = {'user-token': user_token, 'Content-Type': 'application/json'}
    try:
        print("DEBUG: get_or_create_thread - STEP 1: Calling Backendless to get user data...")
        user_url = f"{base_url}/data/Users/{user_object_id}"
        user_response = httpx.get(user_url, headers=headers)
        user_response.raise_for_status()
        user_data = user_response.json()
        print("DEBUG: get_or_create_thread - STEP 1: Success.")

        if 'currentThreadId' in user_data and user_data['currentThreadId']:
            print("DEBUG: get_or_create_thread - STEP 2: Found existing thread ID. Returning.")
            return user_data['currentThreadId']

        print("DEBUG: get_or_create_thread - STEP 2: No thread ID found. Calling OpenAI to create a new thread...")
        thread = openai_client.beta.threads.create()
        new_thread_id = thread.id
        print(f"DEBUG: get_or_create_thread - STEP 2: Success. New thread ID is {new_thread_id}")

        print("DEBUG: get_or_create_thread - STEP 3: Calling Backendless to save the new thread ID...")
        update_payload = {'currentThreadId': new_thread_id}
        update_response = httpx.put(user_url, json=update_payload, headers=headers)
        update_response.raise_for_status()
        print("DEBUG: get_or_create_thread - STEP 3: Success.")

        return new_thread_id
    except Exception as e:
        print(f"An unexpected error occurred in get_or_create_thread: {e}")
        return None


def reset_user_thread(user_token, user_object_id):
    base_url = "https://toughquilt.backendless.app/api"
    headers = {'user-token': user_token, 'Content-Type': 'application/json'}
    try:
        user_url = f"{base_url}/data/Users/{user_object_id}"
        user_response = httpx.get(user_url, headers=headers)
        user_response.raise_for_status()
        user_data = user_response.json()
        current_thread_id = user_data.get('currentThreadId')
        if current_thread_id:
            openai_client.beta.threads.delete(thread_id=current_thread_id)
        update_payload = {'currentThreadId': None}
        update_response = httpx.put(user_url, json=update_payload, headers=headers)
        update_response.raise_for_status()
        return True
    except Exception as e:
        print(f"Error in reset_user_thread: {e}")
        return False


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


@app.route('/ask', methods=['POST'])
def ask():
    user_token = request.headers.get('user-token')
    if not user_token:
        return jsonify({'error': 'User token is missing'}), 401

    data = request.get_json()
    if not data:
        return jsonify({'error': 'Request body is missing'}), 400
    
    prompt = data.get('prompt')
    thread_id = data.get('thread_id')
    object_id = data.get('objectId')
    
    if not prompt or not thread_id or not object_id:
        return jsonify({'error': 'prompt, thread_id, and objectId are required'}), 400

    base_url = "https://toughquilt.backendless.app/api"
    headers = {'user-token': user_token, 'Content-Type': 'application/json'}
    try:
        user_url = f"{base_url}/data/Users/{object_id}"
        user_response = httpx.get(user_url, headers=headers)
        user_response.raise_for_status()
        user_data = user_response.json()

        daily_count = user_data.get('dailyQuestionCount', 0)
        if daily_count >= 100:
            return jsonify({'error': 'Daily limit reached'}), 429

        new_count = daily_count + 1
        update_payload = {'dailyQuestionCount': new_count}
        update_response = httpx.put(user_url, json=update_payload, headers=headers)
        update_response.raise_for_status()

        return Response(stream_with_context(generate_structured_response(thread_id, prompt)), mimetype='application/json')

    except Exception as e:
        print(f"Error in ask endpoint: {e}")
        return jsonify({'error': 'Internal server error'}), 500


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
        
        query_response = httpx.get(query_url, params=query_params)
        query_response.raise_for_status()
        subscriptions = query_response.json()
        
        if not subscriptions:
            return jsonify({'error': f'No subscription found for user {client_reference_id}'}), 404
        
        subscription_object_id = subscriptions[0]['objectId']
        update_url = f"{base_url}/data/Subscriptions/{subscription_object_id}"
        update_payload = {'status': 'active', 'stripeSubscriptionId': subscription_id}
        
        update_response = httpx.put(update_url, json=update_payload)
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
            query_response = httpx.get(query_url, params=query_params)
            query_response.raise_for_status()
            subscriptions = query_response.json()
            
            if not subscriptions:
                return jsonify({'status': 'success'}), 200
            
            subscription_object_id = subscriptions[0]['objectId']
            update_url = f"{base_url}/data/Subscriptions/{subscription_object_id}"
            update_payload = {'status': 'active', 'stripeSubscriptionId': subscription_id}
            
            update_response = httpx.put(update_url, json=update_payload)
            update_response.raise_for_status()
            
        except Exception as e:
            print(f"Error in webhook updating subscription: {e}")
            return jsonify({'status': 'success'}), 200
    
    return jsonify({'status': 'success'}), 200


@app.route('/test-openai')
def test_openai_connection():
    try:
        print("DEBUG: Testing OpenAI connection...")
        # Make a simple, low-cost API call to list models
        openai_client.models.list()
        print("DEBUG: Successfully connected to OpenAI.")
        return jsonify({"status": "success", "message": "Connection to OpenAI API successful."})
    except Exception as e:
        print(f"DEBUG: Failed to connect to OpenAI. Error: {e}")
        return jsonify({"status": "failed", "error": str(e)}), 500


# --- MAIN EXECUTION ---
if __name__ == '__main__':
    app.run(debug=True)
