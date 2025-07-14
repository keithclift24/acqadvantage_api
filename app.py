import requests
import os
from flask import Flask, jsonify, request, Response, stream_with_context
import openai
import stripe
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# --- INITIALIZE SERVICES ---
app = Flask(__name__)
openai_client = openai.OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')


# --- CORE LOGIC FUNCTIONS ---
def stream_assistant_response(thread_id, user_prompt):
    """
    Generator function that streams assistant responses from OpenAI.
    
    Args:
        thread_id (str): The OpenAI thread ID
        user_prompt (str): The user's message/prompt
        
    Yields:
        str: Text chunks from the assistant's response
    """
    try:
        # Create a new message in the thread
        openai_client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=user_prompt
        )
        
        # Create a streaming run for the assistant
        stream = openai_client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id='asst_QUel0QQc2NvKSYZMBCgtStMb',
            stream=True
        )
        
        # Loop through the streaming run event object
        for event in stream:
            if event.event == 'thread.message.delta':
                # Check if delta content exists and has text
                if (hasattr(event, 'data') and 
                    hasattr(event.data, 'delta') and 
                    hasattr(event.data.delta, 'content') and 
                    event.data.delta.content and 
                    len(event.data.delta.content) > 0 and
                    hasattr(event.data.delta.content[0], 'text') and
                    hasattr(event.data.delta.content[0].text, 'value')):
                    
                    yield event.data.delta.content[0].text.value
                    
    except Exception as e:
        print(f"Error in stream_assistant_response: {e}")
        yield f"Error: {str(e)}"


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
        # 1. Get the specific user data using the objectId
        user_url = f"{base_url}/data/Users/{user_object_id}"
        print(f"DEBUG: Fetching user data from: {user_url}")
        user_response = requests.get(user_url, headers=headers)
        user_response.raise_for_status()  # Raise an error for bad status codes
        user_data = user_response.json()

        # 2. Check if the thread ID already exists
        if 'currentThreadId' in user_data and user_data['currentThreadId']:
            print(f"Found existing thread ID: {user_data['currentThreadId']}")
            return user_data['currentThreadId']

        # 3. If not, create a new OpenAI thread
        print("No existing thread ID, creating a new one.")
        thread = openai_client.beta.threads.create()
        new_thread_id = thread.id

        # 4. Update the user record in Backendless with the new thread ID
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
    
    Args:
        user_token (str): The user's authentication token
        user_object_id (str): The user's objectId in Backendless
        
    Returns:
        bool: True on success, False on failure
    """
    base_url = "https://toughquilt.backendless.app/api"
    headers = {
        'user-token': user_token,
        'Content-Type': 'application/json'
    }

    try:
        # 1. Fetch the user's data from Backendless
        user_url = f"{base_url}/data/Users/{user_object_id}"
        user_response = requests.get(user_url, headers=headers)
        user_response.raise_for_status()
        user_data = user_response.json()

        # 2. Check if a currentThreadId exists and is not null
        current_thread_id = user_data.get('currentThreadId')
        if current_thread_id:
            # 3. Delete the thread from OpenAI
            openai_client.beta.threads.delete(thread_id=current_thread_id)
            print(f"Successfully deleted OpenAI thread: {current_thread_id}")

        # 4. Update the user's record in Backendless, setting currentThreadId to None
        update_payload = {'currentThreadId': None}
        update_response = requests.put(user_url, json=update_payload, headers=headers)
        update_response.raise_for_status()

        print(f"Successfully reset thread for user: {user_object_id}")
        return True

    except Exception as e:
        print(f"Error in reset_user_thread: {e}")
        return False


# --- API ENDPOINTS ---
@app.route('/start_chat', methods=['POST'])
def start_chat():
    user_token = request.headers.get('user-token')
    if not user_token:
        return jsonify({'error': 'User token is missing'}), 401

    # Get objectId from the request body
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
    Endpoint to ask questions to the assistant with usage limits and streaming response.
    """
    # 1. Get user-token from headers
    user_token = request.headers.get('user-token')
    if not user_token:
        return jsonify({'error': 'User token is missing'}), 401

    # 2. Get required fields from JSON request body
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Request body is missing'}), 400
    
    prompt = data.get('prompt')
    thread_id = data.get('thread_id')
    object_id = data.get('objectId')
    
    if not prompt or not thread_id or not object_id:
        return jsonify({'error': 'prompt, thread_id, and objectId are required'}), 400

    # 3. Usage Limit Logic
    base_url = "https://toughquilt.backendless.app/api"
    headers = {
        'user-token': user_token,
        'Content-Type': 'application/json'
    }

    try:
        # Fetch user's data from Backendless
        user_url = f"{base_url}/data/Users/{object_id}"
        user_response = requests.get(user_url, headers=headers)
        user_response.raise_for_status()
        user_data = user_response.json()

        # Check daily question count limit
        daily_count = user_data.get('dailyQuestionCount', 0)
        if daily_count >= 10:
            return jsonify({'error': 'Daily limit reached'}), 429

        # Increment the count and save back to Backendless
        new_count = daily_count + 1
        update_payload = {'dailyQuestionCount': new_count}
        update_response = requests.put(user_url, json=update_payload, headers=headers)
        update_response.raise_for_status()

        # 4. Streaming Logic - call the generator function
        def generate():
            for chunk in stream_assistant_response(thread_id, prompt):
                yield chunk

        return Response(stream_with_context(generate()), mimetype='text/plain')

    except Exception as e:
        print(f"Error in ask endpoint: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/create-checkout-session', methods=['POST'])
def create_checkout_session():
    """
    Creates a Stripe checkout session for subscription plans.
    """
    # 1. Get plan_type and objectId from JSON request body
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Request body is missing'}), 400
    
    plan_type = data.get('plan_type')
    user_object_id = data.get('objectId')
    
    if not plan_type:
        return jsonify({'error': 'plan_type is required'}), 400
    
    if not user_object_id:
        return jsonify({'error': 'objectId is required'}), 400

    # 2. Dictionary to look up Stripe Price ID based on plan_type
    price_ids = {
        'monthly': 'price_..._monthly',
        'annual': 'price_..._annual'
    }
    
    if plan_type not in price_ids:
        return jsonify({'error': 'Invalid plan_type'}), 400

    try:
        # 3. Create Stripe checkout session
        checkout_session = stripe.checkout.Session.create(
            mode='subscription',
            line_items=[{
                'price': price_ids[plan_type],
                'quantity': 1,
            }],
            client_reference_id=user_object_id,
            success_url='https://acqadvantage.com/?page=home&session_id={CHECKOUT_SESSION_ID}',
            cancel_url='https://acqadvantage.com/?page=home',
        )

        # 4. Return the checkout URL
        return jsonify({'checkout_url': checkout_session.url})

    except stripe.error.StripeError as e:
        print(f"Stripe error: {e}")
        return jsonify({'error': 'Failed to create checkout session'}), 500
    
    except Exception as e:
        print(f"Unexpected error in create_checkout_session: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/stripe-webhook', methods=['POST'])
def stripe_webhook():
    """
    Handles Stripe webhook events, specifically checkout.session.completed.
    """
    # 1. Get the raw request body and Stripe signature
    payload = request.get_data()
    sig_header = request.headers.get('Stripe-Signature')
    
    if not sig_header:
        return jsonify({'error': 'Missing Stripe-Signature header'}), 400

    try:
        # 2. Securely verify the webhook using Stripe's construct_event
        event = stripe.Webhook.construct_event(
            payload, sig_header, os.getenv('STRIPE_WEBHOOK_SECRET')
        )
    except stripe.error.SignatureVerificationError as e:
        print(f"Webhook signature verification failed: {e}")
        return jsonify({'error': 'Invalid signature'}), 400
    except Exception as e:
        print(f"Error constructing webhook event: {e}")
        return jsonify({'error': 'Webhook error'}), 400

    # 3. Check if the event type is checkout.session.completed
    if event['type'] == 'checkout.session.completed':
        try:
            session = event['data']['object']
            
            # 4. Extract client_reference_id (user's objectId) and subscription ID
            client_reference_id = session.get('client_reference_id')  # User's objectId
            subscription_id = session.get('subscription')
            
            if not client_reference_id or not subscription_id:
                print("Missing client_reference_id or subscription_id in webhook")
                return jsonify({'status': 'success'}), 200
            
            print(f"Processing checkout completion for user: {client_reference_id}")
            
            # 5. Find the subscription record in Backendless where ownerId matches objectId
            base_url = "https://toughquilt.backendless.app/api"
            query_url = f"{base_url}/data/Subscriptions"
            query_params = {
                'where': f"ownerId = '{client_reference_id}'"
            }
            
            try:
                # Find the subscription record by ownerId
                query_response = requests.get(query_url, params=query_params)
                query_response.raise_for_status()
                subscriptions = query_response.json()
                
                if not subscriptions or len(subscriptions) == 0:
                    print(f"Warning: No subscription found for user {client_reference_id}")
                    return jsonify({'status': 'success'}), 200
                
                # Get the subscription record
                subscription_record = subscriptions[0]
                subscription_object_id = subscription_record['objectId']
                
                # 6. Update the subscription record: set status to 'active' and save Stripe subscription ID
                update_url = f"{base_url}/data/Subscriptions/{subscription_object_id}"
                update_payload = {
                    'status': 'active',
                    'stripeSubscriptionId': subscription_id
                }
                
                update_response = requests.put(update_url, json=update_payload)
                update_response.raise_for_status()
                
                print(f"Successfully updated subscription {subscription_object_id} to active status")
                
            except requests.exceptions.RequestException as e:
                print(f"Error updating subscription in Backendless: {e}")
                # Don't fail the webhook - return success to acknowledge receipt
                return jsonify({'status': 'success'}), 200
                
        except Exception as e:
            print(f"Error processing checkout.session.completed event: {e}")
            return jsonify({'status': 'success'}), 200
    
    # 7. Return success response to acknowledge receipt of the webhook
    return jsonify({'status': 'success'}), 200


@app.route('/')
def health_check():
    """A simple health check route."""
    return jsonify({'status': 'API is running'})


# --- MAIN EXECUTION ---
if __name__ == '__main__':
    app.run(debug=True)
