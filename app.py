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
    # 1. Get user_token from Authorization header
    user_token = request.headers.get('Authorization')
    if not user_token:
        return jsonify({'error': 'Authorization token is missing'}), 401

    # 2. Get plan_type from JSON request body
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Request body is missing'}), 400
    
    plan_type = data.get('plan_type')
    if not plan_type or plan_type not in ['monthly', 'annual']:
        return jsonify({'error': 'plan_type must be either "monthly" or "annual"'}), 400

    # 3. Define hardcoded Stripe Price IDs
    price_ids = {
        'monthly': 'price_1MonthlyPlanID',
        'annual': 'price_1AnnualPlanID'
    }

    try:
        # 4. Retrieve current user from Backendless
        backendless_url = "https://api.backendless.com/0EB3F73D-1225-30F9-FFB8-CFD226E65F00/88151BAC-048B-492B-9FE3-3BE69C59937A/users/currentuser"
        headers = {
            'user-token': user_token,
            'Content-Type': 'application/json'
        }
        
        user_response = requests.get(backendless_url, headers=headers)
        user_response.raise_for_status()
        user_data = user_response.json()
        
        # Get the user's objectId
        user_object_id = user_data.get('objectId')
        if not user_object_id:
            return jsonify({'error': 'Unable to retrieve user objectId'}), 400

        # 5. Create Stripe checkout session
        checkout_session = stripe.checkout.Session.create(
            mode='subscription',
            line_items=[{
                'price': price_ids[plan_type],
                'quantity': 1,
            }],
            client_reference_id=user_object_id,
            success_url='https://acqadvantage.com/success',
            cancel_url='https://acqadvantage.com/cancel',
        )

        # 6. Return the checkout URL
        return jsonify({'checkout_url': checkout_session.url})

    except requests.exceptions.RequestException as e:
        print(f"Error retrieving user from Backendless: {e}")
        return jsonify({'error': 'Failed to retrieve user information'}), 500
    
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
        # 2. Verify the webhook signature
        event = stripe.Webhook.construct_event(
            payload, sig_header, os.getenv('STRIPE_WEBHOOK_SECRET')
        )
    except stripe.error.SignatureVerificationError as e:
        print(f"Webhook signature verification failed: {e}")
        return jsonify({'error': 'Invalid signature'}), 400
    except Exception as e:
        print(f"Error constructing webhook event: {e}")
        return jsonify({'error': 'Webhook error'}), 400

    # 3. Handle the checkout.session.completed event
    if event['type'] == 'checkout.session.completed':
        try:
            session = event['data']['object']
            
            # Extract required data from the session
            client_reference_id = session.get('client_reference_id')  # User's objectId
            subscription_id = session.get('subscription')
            
            if not client_reference_id or not subscription_id:
                print("Missing client_reference_id or subscription_id in webhook")
                return jsonify({}), 200
            
            print(f"Processing checkout completion for user: {client_reference_id}")
            
            # 4. Connect to Backendless to update subscription
            base_url = "https://toughquilt.backendless.app/api"
            
            # First, we need to get an admin token or use a service account
            # For now, we'll use the REST API without authentication for the webhook
            # In production, you should use a service account or admin credentials
            
            # Query the Subscriptions table to find the trialing subscription
            query_url = f"{base_url}/data/Subscriptions"
            query_params = {
                'where': f"ownerId = '{client_reference_id}' AND status = 'trialing'"
            }
            
            try:
                # Find the trialing subscription record
                query_response = requests.get(query_url, params=query_params)
                query_response.raise_for_status()
                subscriptions = query_response.json()
                
                if not subscriptions or len(subscriptions) == 0:
                    print(f"Warning: No trialing subscription found for user {client_reference_id}")
                    return jsonify({}), 200
                
                # Get the first (should be only) trialing subscription
                subscription_record = subscriptions[0]
                subscription_object_id = subscription_record['objectId']
                
                # Update the subscription record
                update_url = f"{base_url}/data/Subscriptions/{subscription_object_id}"
                update_payload = {
                    'stripeSubscriptionId': subscription_id,
                    'status': 'active'
                }
                
                update_response = requests.put(update_url, json=update_payload)
                update_response.raise_for_status()
                
                print(f"Successfully updated subscription {subscription_object_id} to active status")
                
            except requests.exceptions.RequestException as e:
                print(f"Error updating subscription in Backendless: {e}")
                # Don't fail the webhook - Stripe expects a 200 response
                return jsonify({}), 200
                
        except Exception as e:
            print(f"Error processing checkout.session.completed event: {e}")
            return jsonify({}), 200
    
    # 5. Return 200 status to acknowledge receipt
    return jsonify({}), 200


@app.route('/')
def health_check():
    """A simple health check route."""
    return jsonify({'status': 'API is running'})


# --- MAIN EXECUTION ---
if __name__ == '__main__':
    app.run(debug=True)
