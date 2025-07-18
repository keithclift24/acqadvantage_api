import requests
import os
from flask import Flask, jsonify, request, Response
import openai
import json
import time # Import the time module for polling
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
def get_structured_assistant_response(thread_id, user_prompt):
    """
    Runs the assistant, waits for completion, and extracts the final JSON object.
    This replaces the streaming function for structured responses.
    """
    try:
        # Create a new message in the thread
        openai_client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=user_prompt
        )
        
        # Create a non-streaming run
        run = openai_client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id='asst_QUel0QQc2NvKSYZMBCgtStMb' # Your Assistant ID
        )

        # Poll for the run to complete
        while run.status in ['queued', 'in_progress', 'cancelling']:
            time.sleep(1) # Wait for 1 second before checking again
            run = openai_client.beta.threads.runs.retrieve(
                thread_id=thread_id,
                run_id=run.id
            )

        if run.status == 'completed':
            messages = openai_client.beta.threads.messages.list(thread_id=thread_id)
            # The latest message is the first in the list
            assistant_message_content = messages.data[0].content[0].text.value
            
            # --- Robust JSON Extraction ---
            # Find the start and end of the JSON object in the response string
            try:
                start_index = assistant_message_content.index('{')
                end_index = assistant_message_content.rindex('}') + 1
                json_string = assistant_message_content[start_index:end_index]
                # This clean string is what we'll return
                return json_string
            except ValueError:
                print("Error: Could not find a valid JSON object in the assistant's response.")
                return json.dumps({"error": "Failed to extract valid JSON from response."})
        else:
            print(f"Run failed with status: {run.status}")
            return json.dumps({"error": f"Run failed with status: {run.status}"})

    except Exception as e:
        print(f"Error in get_structured_assistant_response: {e}")
        return json.dumps({"error": f"An error occurred: {str(e)}"})


def get_or_create_thread(user_token, user_object_id):
    """
    Gets a user's thread_id from Backendless using their objectId,
    or creates a new one.
    """
    base_url = "https://toughquilt.backendless.app/api"
    headers = {
        'user-token': user_token,
        'Content-Type': 'application/json'
    }
    try:
        user_url = f"{base_url}/data/Users/{user_object_id}"
        user_response = requests.get(user_url, headers=headers)
        user_response.raise_for_status()
        user_data = user_response.json()
        if 'currentThreadId' in user_data and user_data['currentThreadId']:
            print(f"Found existing thread ID: {user_data['currentThreadId']}")
            return user_data['currentThreadId']
        print("No existing thread ID, creating a new one.")
        thread = openai_client.beta.threads.create()
        new_thread_id = thread.id
        update_payload = {'currentThreadId': new_thread_id}
        update_response = requests.put(user_url, json=update_payload, headers=headers)
        update_response.raise_for_status()
        print(f"Successfully created and saved new thread ID: {new_thread_id}")
        return new_thread_id
    except Exception as e:
        print(f"An unexpected error occurred in get_or_create_thread: {e}")
        return None


def reset_user_thread(user_token, user_object_id):
    """
    Resets a user's conversation thread by deleting the existing OpenAI thread
    and clearing the currentThreadId from Backendless.
    """
    base_url = "https://toughquilt.backendless.app/api"
    headers = {
        'user-token': user_token,
        'Content-Type': 'application/json'
    }
    try:
        user_url = f"{base_url}/data/Users/{user_object_id}"
        user_response = requests.get(user_url, headers=headers)
        user_response.raise_for_status()
        user_data = user_response.json()
        current_thread_id = user_data.get('currentThreadId')
        if current_thread_id:
            openai_client.beta.threads.delete(thread_id=current_thread_id)
            print(f"Successfully deleted OpenAI thread: {current_thread_id}")
        update_payload = {'currentThreadId': None}
        update_response = requests.put(user_url, json=update_payload, headers=headers)
        update_response.raise_for_status()
        print(f"Successfully reset thread for user: {user_object_id}")
        return True
    except Exception as e:
        print(f"Error in reset_user_thread: {e}")
        return False


# --- API ENDPOINTS ---
@app.route('/')
def health_check():
    """A simple health check route."""
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
    """
    Endpoint to ask questions. It now returns a single, complete JSON object
    by waiting for the assistant's run to finish.
    """
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
    headers = {
        'user-token': user_token,
        'Content-Type': 'application/json'
    }
    try:
        user_url = f"{base_url}/data/Users/{object_id}"
        user_response = requests.get(user_url, headers=headers)
        user_response.raise_for_status()
        user_data = user_response.json()

        daily_count = user_data.get('dailyQuestionCount', 0)
        if daily_count >= 100: # Increased limit for testing
            return jsonify({'error': 'Daily limit reached'}), 429

        new_count = daily_count + 1
        update_payload = {'dailyQuestionCount': new_count}
        update_response = requests.put(user_url, json=update_payload, headers=headers)
        update_response.raise_for_status()

        json_response_string = get_structured_assistant_response(thread_id, prompt)
        
        return Response(json_response_string, mimetype='application/json')

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
            line_items=[{
                'price': price_ids[plan_type],
                'quantity': 1,
            }]
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
            return jsonify({
                'error': 'Payment not successful',
                'session_status': session.status,
                'payment_status': session.payment_status
            }), 400
        
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
        
    except stripe.error.StripeError as e:
        return jsonify({'error': f'Stripe error: {str(e)}'}), 400
    except requests.exceptions.RequestException as e:
        return jsonify({'error': 'Database error'}), 500
    except Exception as e:
        return jsonify({'error': 'Internal server error'}), 500


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
    except stripe.error.SignatureVerificationError as e:
        return jsonify({'error': 'Invalid signature'}), 400
    except Exception as e:
        return jsonify({'error': 'Webhook error'}), 400

    if event['type'] == 'checkout.session.completed':
        try:
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
                
            except requests.exceptions.RequestException as e:
                print(f"Error updating subscription in Backendless: {e}")
                return jsonify({'status': 'success'}), 200
                
        except Exception as e:
            print(f"Error processing checkout.session.completed event: {e}")
            return jsonify({'status': 'success'}), 200
    
    return jsonify({'status': 'success'}), 200


# --- MAIN EXECUTION ---
if __name__ == '__main__':
    app.run(debug=True)
