# ======================================================================================
# AcqAdvantage API Server
#
# This Flask application serves as the backend for AcqAdvantage. It integrates with
# several external services to provide its core functionality:
#
# - OpenAI Assistants API: For powering the conversational AI chat, generating
#   structured, intelligent responses to user prompts.
# - Backendless: As the primary database for user management, storing user data,
#   conversation thread IDs, and subscription statuses.
# - Stripe: For handling subscription payments, including creating checkout
#   sessions and managing payment lifecycle events via webhooks.
#
# The API exposes endpoints for chat interactions, user thread management, and
# payment processing.
# ======================================================================================

import requests
import os
from flask import Flask, jsonify, request, Response, stream_with_context
import openai
import json
import time
import stripe
from dotenv import load_dotenv
from flask_cors import CORS
import logging

# Load environment variables from a .env file for secure configuration.
# This is crucial for keeping API keys and secrets out of the source code.
load_dotenv()

# --- INITIALIZE SERVICES ---
# Initialize the Flask application and configure it for Cross-Origin Resource Sharing (CORS)
# to allow requests from the frontend domain.
app = Flask(__name__)
CORS(app)

# Initialize the OpenAI client with the API key from environment variables.
openai_client = openai.OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

# Set the Stripe API key from environment variables to authenticate Stripe API calls.
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')


# --- CORE LOGIC FUNCTIONS ---

def generate_structured_response(thread_id, user_prompt):
    """
    Manages the interaction with the OpenAI Assistant and streams the response.

    This generator function performs the following steps:
    1. Adds the user's new message to the specified conversation thread.
    2. Creates a new "run" for the assistant to process the thread.
    3. Polls the run's status, waiting for it to complete. During polling, it
       sends a whitespace "heartbeat" every second. This prevents the client-side
       connection from timing out on long-running assistant tasks.
    4. Once the run is complete, it retrieves the latest assistant message.
    5. It extracts the JSON object from the assistant's text response.
    6. It yields the final, complete JSON string as the last item in the stream.

    Args:
        thread_id (str): The ID of the OpenAI Assistant thread.
        user_prompt (str): The prompt/message from the user.

    Yields:
        str: A whitespace character during polling, and the final JSON response string.
    """
    try:
        # Add the user's message to the existing thread
        openai_client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=user_prompt
        )

        # Create a run to have the assistant process the thread.
        # The assistant_id is hardcoded to our specific AcqAdvantage assistant.
        run = openai_client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id='asst_QUel0QQc2NvKSYZMBCgtStMb'  # The specific Assistant ID to use
        )

        # Poll for the run to complete, sending a heartbeat to keep the connection alive.
        # This is critical for preventing timeouts on the client-side during long-running tasks.
        while run.status in ['queued', 'in_progress', 'cancelling']:
            time.sleep(1)
            run = openai_client.beta.threads.runs.retrieve(
                thread_id=thread_id,
                run_id=run.id
            )
            # --- HEARTBEAT ---
            # Yield a single space to prevent client-side request timeouts.
            yield ' '

        # If the run completed successfully, process and yield the response
        if run.status == 'completed':
            messages = openai_client.beta.threads.messages.list(thread_id=thread_id)
            assistant_message_content = messages.data[0].content[0].text.value

            # Extract the JSON part of the response, as the assistant may add other text.
            try:
                start_index = assistant_message_content.index('{')
                end_index = assistant_message_content.rindex('}') + 1
                json_string = assistant_message_content[start_index:end_index]
                yield json_string  # Yield the final, complete JSON string
            except ValueError:
                print("Error: Could not find a valid JSON object in the assistant's response.")
                yield json.dumps({"error": "Failed to extract valid JSON from response."})
        else:
            # Handle cases where the run did not complete successfully
            print(f"Run failed with status: {run.status}")
            yield json.dumps({"error": f"Run failed with status: {run.status}"})

    except Exception as e:
        print(f"Error in generate_structured_response: {e}")
        yield json.dumps({"error": f"An error occurred: {str(e)}"})


def get_or_create_thread(user_token, user_object_id):
    logging.basicConfig(level=logging.INFO, force=True)
    logging.info(f"--- Starting get_or_create_thread for objectId: {user_object_id} ---")
    base_url = "https://toughquilt.backendless.app/api"
    headers = {'user-token': user_token, 'Content-Type': 'application/json'}
    user_url = f"{base_url}/data/Users/{user_object_id}"
    try:
        # Step 1: Fetch user data from Backendless
        logging.info("Step 1: Fetching user data from Backendless.")
        user_response = requests.get(user_url, headers=headers)
        user_response.raise_for_status()
        user_data = user_response.json()
        logging.info("Step 1 SUCCESS: User data fetched.")
        # Step 2: Check for existing thread
        if 'currentThreadId' in user_data and user_data['currentThreadId']:
            existing_thread_id = user_data['currentThreadId']
            logging.info(f"Step 2 SUCCESS: Found existing threadId: {existing_thread_id}")
            return existing_thread_id
        # Step 3: Create new OpenAI thread
        logging.info("Step 3: No existing thread found. Creating new thread with OpenAI.")
        thread = openai_client.beta.threads.create()
        new_thread_id = thread.id
        logging.info(f"Step 3 SUCCESS: Created new threadId: {new_thread_id}")
        # Step 4: Update user in Backendless
        logging.info(f"Step 4: Updating user record in Backendless with new threadId.")
        update_payload = {'currentThreadId': new_thread_id}
        update_response = requests.put(user_url, json=update_payload, headers=headers)
        update_response.raise_for_status()
        logging.info("Step 4 SUCCESS: User record updated.")
        
        return new_thread_id
    except Exception as e:
        logging.error(f"--- An unexpected error occurred in get_or_create_thread: {e} ---", exc_info=True)
        return None


def reset_user_thread(user_token, user_object_id):
    """
    Deletes a user's current OpenAI thread and clears the reference in Backendless. 

    This is used when a user wants to start a fresh conversation. It fetches the
    current thread ID from Backendless, deletes it from OpenAI's servers, and then
    sets the 'currentThreadId' field in the user's Backendless record to null.

    Args:
        user_token (str): The authentication token for the user.
        user_object_id (str): The unique ID of the user in the Backendless database.

    Returns:
        bool: True if the thread was reset successfully, False otherwise.
    """
    base_url = "https://toughquilt.backendless.app/api"
    # The 'user-token' is required by Backendless for authenticating the user.
    headers = {'user-token': user_token, 'Content-Type': 'application/json'}
    try:
        # Fetch user data from Backendless to get the current thread ID.
        user_url = f"{base_url}/data/Users/{user_object_id}"
        user_response = requests.get(user_url, headers=headers)
        user_response.raise_for_status()
        user_data = user_response.json()

        # If a thread exists, delete it from OpenAI
        current_thread_id = user_data.get('currentThreadId')
        if current_thread_id:
            openai_client.beta.threads.delete(thread_id=current_thread_id)

        # Update the user's record in Backendless to remove the thread ID
        update_payload = {'currentThreadId': None}
        update_response = requests.put(user_url, json=update_payload, headers=headers)
        update_response.raise_for_status()
        return True
    except Exception as e:
        print(f"Error in reset_user_thread: {e}")
        return False


# --- API ENDPOINTS ---

@app.route('/')
def health_check():
    """
    A simple health check endpoint to confirm the API is running.
    Returns a JSON object with a status message.
    """
    return jsonify({'status': 'API is running'})


@app.route('/start_chat', methods=['POST'])
def start_chat():
    """
    Endpoint to initialize or retrieve a chat session for a user.

    This endpoint ensures that a user has a valid OpenAI thread ID before they
    start sending messages. It's the first step in the chat workflow.

    Requires:
    - 'user-token' in headers.
    - JSON body with 'objectId'.

    Returns:
    - JSON with 'thread_id' on success.
    - Error JSON on failure.
    """
    # Ensure the user token is present in the request headers for authentication.
    user_token = request.headers.get('user-token')
    if not user_token:
        return jsonify({'error': 'User token is missing'}), 401

    # Ensure the request body contains valid JSON.
    data = request.get_json()
    if not data or 'objectId' not in data:
        return jsonify({'error': 'objectId is missing from request body'}), 400

    # Retrieve the user's objectId and get or create a thread for them.
    user_object_id = data['objectId']
    thread_id = get_or_create_thread(user_token, user_object_id)

    # Return the thread_id to the client.
    if thread_id:
        return jsonify({'thread_id': thread_id})
    else:
        return jsonify({'error': 'Failed to process request'}), 500


@app.route('/ask', methods=['POST'])
def ask():
    """
    The main endpoint for sending a user's prompt to the assistant.
    It performs several checks before streaming the response:
    1. Validates user token and request payload.
    2. Fetches user data from Backendless to check the daily question count.
    3. Enforces a daily limit (e.g., 100 questions).
    4. Increments the user's question count in Backendless.
    5. Streams the response from generate_structured_response.

    Requires:
    - 'user-token' in headers.
    - JSON body with 'prompt', 'thread_id', and 'objectId'.

    Returns:
    - A streaming JSON response on success.
    - Error JSON on failure or if the daily limit is reached.
    """
    # Ensure the user token is present in the request headers for authentication.
    user_token = request.headers.get('user-token')
    if not user_token:
        return jsonify({'error': 'User token is missing'}), 401

    # Ensure the request body contains valid JSON.
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Request body is missing'}), 400

    # Extract required data from the JSON payload.
    prompt = data.get('prompt')
    thread_id = data.get('thread_id')
    object_id = data.get('objectId')

    if not prompt or not thread_id or not object_id:
        return jsonify({'error': 'prompt, thread_id, and objectId are required'}), 400

    # Prepare for Backendless API call.
    base_url = "https://toughquilt.backendless.app/api"
    headers = {'user-token': user_token, 'Content-Type': 'application/json'}
    try:
        # Fetch user data from Backendless to check subscription status and daily limits.
        user_url = f"{base_url}/data/Users/{object_id}"
        user_response = requests.get(user_url, headers=headers)
        user_response.raise_for_status()
        user_data = user_response.json()

        # Enforce a daily question limit to prevent abuse.
        # This is a simple form of rate limiting.
        daily_count = user_data.get('dailyQuestionCount', 0)
        if daily_count >= 100:
            return jsonify({'error': 'Daily limit reached'}), 429

        # Increment the user's daily question count in Backendless.
        new_count = daily_count + 1
        update_payload = {'dailyQuestionCount': new_count}
        update_response = requests.put(user_url, json=update_payload, headers=headers)
        update_response.raise_for_status()

        # Stream the assistant's response
        return Response(stream_with_context(generate_structured_response(thread_id, prompt)), mimetype='application/json')

    except Exception as e:
        print(f"Error in ask endpoint: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/reset_thread', methods=['POST'])
def reset_thread():
    """
    Endpoint to reset a user's conversation thread.

    Requires:
    - 'user-token' in headers.
    - JSON body with 'objectId'.

    Returns:
    - Success or failure JSON message.
    """
    # Ensure the user token is present in the request headers for authentication.
    user_token = request.headers.get('user-token')
    if not user_token:
        return jsonify({'error': 'User token is missing'}), 401

    # Ensure the request body contains valid JSON.
    data = request.get_json()
    if not data or 'objectId' not in data:
        return jsonify({'error': 'objectId is missing from request body'}), 400

    # Retrieve the user's objectId and reset their thread.
    user_object_id = data['objectId']
    success = reset_user_thread(user_token, user_object_id)

    # Return a success or failure message to the client.
    if success:
        return jsonify({'status': 'success', 'message': 'Thread reset successfully'})
    else:
        return jsonify({'status': 'failure', 'message': 'Failed to reset thread'}), 500


@app.route('/create-checkout-session', methods=['POST'])
def create_checkout_session():
    """
    Creates a Stripe Checkout session for a subscription.

    It takes a plan type ('monthly' or 'annual') and a user's objectId,
    then generates a Stripe Checkout URL that the frontend can redirect to.
    The user's objectId is passed as 'client_reference_id' to link the
    Stripe session back to the user.

    Requires:
    - JSON body with 'planType' and 'objectId'.

    Returns:
    - JSON with 'checkout_url' on success.
    - Error JSON on failure.
    """
    # Ensure the request body contains valid JSON.
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Request body is missing'}), 400

    # Extract required data from the JSON payload.
    plan_type = data.get('planType')
    user_object_id = data.get('objectId')

    if not plan_type or not user_object_id:
        return jsonify({'error': 'planType and objectId are required'}), 400

    # Map plan types from the frontend to specific Stripe Price IDs.
    # These IDs are configured in the Stripe dashboard.
    price_ids = {
        'monthly': 'price_1Rl2mc2Lfw5u3Q4QuJGFFgiG',
        'annual': 'price_1Rl2pB2Lfw5u3Q4QFpW9Olha'
    }

    if plan_type not in price_ids:
        return jsonify({'error': 'Invalid plan_type'}), 400

    try:
        # Create the Stripe Checkout Session.
        # The 'client_reference_id' is crucial as it links the Stripe session
        # back to our user's objectId in Backendless.
        session = stripe.checkout.Session.create(
            mode='subscription',
            success_url='https://acqadvantage.com/?payment=success',
            cancel_url='https://acqadvantage.com/?page=home',
            client_reference_id=user_object_id,  # Link session to user
            line_items=[{'price': price_ids[plan_type], 'quantity': 1}]
        )
        return jsonify({'checkout_url': session.url})
    except Exception as e:
        print(f"Error creating Stripe session: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/verify-payment-session', methods=['POST'])
def verify_payment_session():
    """
    Verifies a Stripe payment session and updates the user's subscription status.

    After a user successfully pays via Stripe Checkout, the frontend calls this
    endpoint with the session_id. This function retrieves the session from Stripe,
    confirms payment was successful, and then updates the corresponding user's
    subscription record in Backendless to 'active'.

    Requires:
    - JSON body with 'session_id'.

    Returns:
    - Success JSON on successful verification and update.
    - Error JSON on failure.
    """
    # Ensure the request body contains valid JSON.
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Request body is missing'}), 400

    # Extract the session_id from the JSON payload.
    session_id = data.get('session_id')
    if not session_id:
        return jsonify({'error': 'session_id is required'}), 400

    try:
        # Retrieve the session from Stripe to get payment details.
        session = stripe.checkout.Session.retrieve(session_id)

        # Verify that the payment was successful before granting access.
        if session.status != 'complete' or session.payment_status != 'paid':
            return jsonify({'error': 'Payment not successful'}), 400

        client_reference_id = session.client_reference_id
        subscription_id = session.subscription

        if not client_reference_id or not subscription_id:
            return jsonify({'error': 'Missing client_reference_id or subscription_id in session'}), 400

        # Use the client_reference_id to find the user's subscription record in Backendless.
        base_url = "https://toughquilt.backendless.app/api"
        query_url = f"{base_url}/data/Subscriptions"
        query_params = {'where': f"ownerId.objectId = '{client_reference_id}'"}

        query_response = requests.get(query_url, params=query_params)
        query_response.raise_for_status()
        subscriptions = query_response.json()

        if not subscriptions:
            return jsonify({'error': f'No subscription found for user {client_reference_id}'}), 404

        # Update the subscription record to active
        subscription_object_id = subscriptions[0]['objectId']
        update_url = f"{base_url}/data/Subscriptions/{subscription_object_id}"
        update_payload = {'status': 'active', 'stripeSubscriptionId': subscription_id}

        update_response = requests.put(update_url, json=update_payload)
        update_response.raise_for_status()

        # Return a success status to the client.
        return jsonify({'status': 'success'}), 200

    except Exception as e:
        return jsonify({'error': f'Server error: {str(e)}'}), 500


@app.route('/stripe-webhook', methods=['POST'])
def stripe_webhook():
    """
    Handles incoming webhooks from Stripe for various events.

    This endpoint listens for events from Stripe, primarily to handle the
    'checkout.session.completed' event. This serves as a server-side confirmation
    of payment, providing a reliable way to update subscription statuses even if
    the client-side verification fails.

    Requires:
    - 'Stripe-Signature' in headers for verification.
    - Raw request body (payload).

    Returns:
    - 200 OK on success, 400 on error.
    """
    # Retrieve the raw payload and the Stripe-Signature header.
    payload = request.get_data()
    sig_header = request.headers.get('Stripe-Signature')

    if not sig_header:
        return jsonify({'error': 'Missing Stripe-Signature header'}), 400

    try:
        # Verify the event came from Stripe
        event = stripe.Webhook.construct_event(
            payload, sig_header, os.getenv('STRIPE_WEBHOOK_SECRET')
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 400

    # Handle the 'checkout.session.completed' event
    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        client_reference_id = session.get('client_reference_id')
        subscription_id = session.get('subscription')

        if not client_reference_id or not subscription_id:
            # Acknowledge receipt even if data is missing to prevent Stripe from retrying.
            return jsonify({'status': 'success'}), 200

        # Find and update the user's subscription in Backendless using the client_reference_id.
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
            # Return success to prevent Stripe from resending the webhook
            return jsonify({'status': 'success'}), 200

    # Acknowledge other event types to prevent Stripe from resending.
    return jsonify({'status': 'success'}), 200


# --- MAIN EXECUTION ---
# This block runs the application when the script is executed directly.
if __name__ == '__main__':
    # Runs the Flask app in debug mode for development.
    # In a production environment, a proper WSGI server like Gunicorn or uWSGI should be used.
    app.run(debug=True)
