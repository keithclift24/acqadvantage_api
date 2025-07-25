# === IMPORT STATEMENTS ===
# These lines import external libraries (code packages) that our application needs to work
import pandas as pd  # Already imported, but ensure it's near the top if not
import httpx          # Library for making HTTP requests to other websites/APIs
import os             # Library for accessing operating system features like environment variables
from flask import Flask, jsonify, request, Response, stream_with_context  # Flask web framework components
import openai         # Official OpenAI library for AI conversations
import json           # Library for working with JSON data format
import time           # Library for time-related functions like delays
import stripe         # Official Stripe library for payment processing
from dotenv import load_dotenv  # Library to load secret keys from .env file
from flask_cors import CORS     # Library to handle cross-origin requests (allows websites to call our API)

# === LOAD CONFIGURATION ===
# Load secret keys and configuration from .env file
# This keeps sensitive information (like API keys) out of the main code
load_dotenv()

# === INITIALIZE SERVICES ===
# Set up the main components our application will use

# Create the main Flask web application
app = Flask(__name__)

# Enable CORS - this allows websites from different domains to use our API
CORS(app)

# Set up OpenAI client with our API key (retrieved from environment variables)
# This allows us to communicate with OpenAI's AI assistant
openai_client = openai.OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

# Set up Stripe for payment processing using our secret key
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')


# === CORE LOGIC FUNCTIONS ===
# These are the main functions that handle the business logic of our application

def generate_structured_response(thread_id, user_prompt):
    """
    WHAT THIS FUNCTION DOES:
    This function handles communication with OpenAI's AI assistant. It's like having a conversation
    where you send a message and wait for a response, but it can take time so we keep the 
    connection alive by sending periodic "heartbeats".
    
    PARAMETERS:
    - thread_id: A unique ID that represents an ongoing conversation with the AI
    - user_prompt: The question or message the user wants to send to the AI
    
    HOW IT WORKS:
    1. Sends the user's message to the AI assistant
    2. Starts a "run" (AI processing the message)
    3. Waits for the AI to finish, sending heartbeats to prevent timeout
    4. Returns the AI's response in JSON format
    """
    try:
        # STEP 1: Send the user's message to the AI conversation thread
        # This is like adding a new message to an ongoing chat conversation
        openai_client.beta.threads.messages.create(
            thread_id=thread_id,     # Which conversation this message belongs to
            role="user",             # Identifies this as a message from the user (not the AI)
            content=user_prompt      # The actual message content
        )
        
        # STEP 2: Start the AI processing ("run") to generate a response
        # This tells OpenAI's AI assistant to read the conversation and respond
        run = openai_client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id='asst_QUel0QQc2NvKSYZMBCgtStMb'  # Your specific AI assistant ID
        )

        # STEP 3: Wait for the AI to finish processing, but keep the connection alive
        # AI processing can take time, so we check the status repeatedly
        while run.status in ['queued', 'in_progress', 'cancelling']:
            time.sleep(1)  # Wait 1 second before checking again
            
            # Check the current status of the AI processing
            run = openai_client.beta.threads.runs.retrieve(
                thread_id=thread_id,
                run_id=run.id
            )
            
            # HEARTBEAT: Send a space character to prevent the web connection from timing out
            # This keeps the connection alive while waiting for the AI response
            yield ' ' 

        # STEP 4: Process the AI's response once it's complete
        if run.status == 'completed':
            # Get all messages from the conversation (the AI's response will be the newest)
            messages = openai_client.beta.threads.messages.list(thread_id=thread_id)
            
            # Extract the AI's response text (it's the first/newest message)
            assistant_message_content = messages.data[0].content[0].text.value
            
            try:
                # STEP 5: Extract just the JSON part from the AI's response
                # The AI might include extra text, but we only want the structured data
                start_index = assistant_message_content.index('{')      # Find first '{'
                end_index = assistant_message_content.rindex('}') + 1   # Find last '}'
                json_string = assistant_message_content[start_index:end_index]
                
                # Send the final JSON response back to the user
                yield json_string
                
            except ValueError:
                # If we can't find valid JSON in the response, return an error
                print("Error: Could not find a valid JSON object in the assistant's response.")
                yield json.dumps({"error": "Failed to extract valid JSON from response."})
        else:
            # If the AI processing failed for some reason, return an error
            print(f"Run failed with status: {run.status}")
            yield json.dumps({"error": f"Run failed with status: {run.status}"})

    except Exception as e:
        # If anything goes wrong anywhere in this process, return an error message
        print(f"Error in generate_structured_response: {e}")
        yield json.dumps({"error": f"An error occurred: {str(e)}"})


def get_or_create_thread(user_token, user_object_id):
    """
    WHAT THIS FUNCTION DOES:
    This function manages conversation threads for users. Think of a "thread" as an ongoing 
    conversation with the AI. If a user already has a conversation going, we continue it.
    If they don't have one, we create a new conversation for them.
    
    PARAMETERS:
    - user_token: A security token that proves the user is authenticated
    - user_object_id: The unique ID that identifies this specific user
    
    HOW IT WORKS:
    1. Check if the user already has an ongoing conversation (thread)
    2. If yes, return that conversation ID
    3. If no, create a new conversation and save it to the user's profile
    """
    
    # Set up the connection details for our user database (Backendless)
    base_url = "https://toughquilt.backendless.app/api"
    headers = {'user-token': user_token, 'Content-Type': 'application/json'}
    
    try:
        # STEP 1: Get the user's current information from the database
        print("DEBUG: get_or_create_thread - STEP 1: Calling Backendless to get user data...")
        user_url = f"{base_url}/data/Users/{user_object_id}"
        
        # Make a request to get the user's data
        user_response = httpx.get(user_url, headers=headers)
        user_response.raise_for_status()  # This will throw an error if the request failed
        user_data = user_response.json()  # Convert the response to a Python dictionary
        print("DEBUG: get_or_create_thread - STEP 1: Success.")

        # STEP 2: Check if the user already has an active conversation thread
        if 'currentThreadId' in user_data and user_data['currentThreadId']:
            # User already has a conversation going, so return that thread ID
            print("DEBUG: get_or_create_thread - STEP 2: Found existing thread ID. Returning.")
            return user_data['currentThreadId']

        # STEP 3: User doesn't have a conversation, so create a new one
        print("DEBUG: get_or_create_thread - STEP 2: No thread ID found. Calling OpenAI to create a new thread...")
        thread = openai_client.beta.threads.create()  # Ask OpenAI to create a new conversation
        new_thread_id = thread.id                      # Get the ID of the new conversation
        print(f"DEBUG: get_or_create_thread - STEP 2: Success. New thread ID is {new_thread_id}")

        # STEP 4: Save the new conversation ID to the user's profile in the database
        print("DEBUG: get_or_create_thread - STEP 3: Calling Backendless to save the new thread ID...")
        update_payload = {'currentThreadId': new_thread_id}  # Data to update
        update_response = httpx.put(user_url, json=update_payload, headers=headers)
        update_response.raise_for_status()  # Check if the update was successful
        print("DEBUG: get_or_create_thread - STEP 3: Success.")

        # Return the new conversation ID
        return new_thread_id
        
    except Exception as e:
        # If anything goes wrong, log the error and return None (meaning failure)
        print(f"An unexpected error occurred in get_or_create_thread: {e}")
        return None


def reset_user_thread(user_token, user_object_id):
    """
    WHAT THIS FUNCTION DOES:
    This function completely resets a user's conversation with the AI. It's like starting 
    fresh - deletes the old conversation and clears the user's record so they can start 
    a brand new conversation next time.
    
    PARAMETERS:
    - user_token: A security token that proves the user is authenticated
    - user_object_id: The unique ID that identifies this specific user
    
    HOW IT WORKS:
    1. Get the user's current conversation ID from the database
    2. Delete that conversation from OpenAI
    3. Clear the conversation ID from the user's profile
    """
    
    # Set up the connection details for our user database (Backendless)
    base_url = "https://toughquilt.backendless.app/api"
    headers = {'user-token': user_token, 'Content-Type': 'application/json'}
    
    try:
        # STEP 1: Get the user's current information from the database
        user_url = f"{base_url}/data/Users/{user_object_id}"
        user_response = httpx.get(user_url, headers=headers)
        user_response.raise_for_status()  # Check if the request was successful
        user_data = user_response.json()  # Convert response to Python dictionary
        
        # STEP 2: Get the user's current conversation ID (if they have one)
        current_thread_id = user_data.get('currentThreadId')
        
        # STEP 3: If the user has an active conversation, delete it from OpenAI
        if current_thread_id:
            openai_client.beta.threads.delete(thread_id=current_thread_id)
        
        # STEP 4: Clear the conversation ID from the user's profile in our database
        # This sets their currentThreadId to None (empty/null)
        update_payload = {'currentThreadId': None}
        update_response = httpx.put(user_url, json=update_payload, headers=headers)
        update_response.raise_for_status()  # Check if the update was successful
        
        # Return True to indicate success
        return True
        
    except Exception as e:
        # If anything goes wrong, log the error and return False (indicating failure)
        print(f"Error in reset_user_thread: {e}")
        return False


# === API ENDPOINTS ===
# These are the web addresses (URLs) that other applications can call to use our service
# Each endpoint is like a specific function that can be accessed over the internet

@app.route('/')
def health_check():
    """
    WHAT THIS ENDPOINT DOES:
    This is a simple "health check" endpoint. It's like asking "Are you there?" 
    and getting back "Yes, I'm running fine!" It's used to test if the API is working.
    
    URL: GET /
    RETURNS: A JSON message saying the API is running
    """
    return jsonify({'status': 'API is running'})


@app.route('/start_chat', methods=['POST'])
def start_chat():
    """
    WHAT THIS ENDPOINT DOES:
    This endpoint initializes a conversation with the AI for a specific user. 
    It either finds their existing conversation or creates a new one for them.
    
    URL: POST /start_chat
    REQUIRES: 
    - user-token in the request headers (for authentication)
    - objectId in the request body (the user's unique ID)
    
    RETURNS: A thread_id that represents the conversation
    """
    
    # STEP 1: Check authentication - make sure the user is logged in
    user_token = request.headers.get('user-token')  # Get the authentication token from headers
    if not user_token:
        # If no token provided, return an error (401 = Unauthorized)
        return jsonify({'error': 'User token is missing'}), 401
    
    # STEP 2: Get the request data and validate it
    data = request.get_json()  # Get the JSON data from the request body
    if not data or 'objectId' not in data:
        # If no data or missing objectId, return an error (400 = Bad Request)
        return jsonify({'error': 'objectId is missing from request body'}), 400
    
    # STEP 3: Extract the user's unique ID from the request
    user_object_id = data['objectId']
    
    # STEP 4: Get or create a conversation thread for this user
    thread_id = get_or_create_thread(user_token, user_object_id)
    
    # STEP 5: Return the result
    if thread_id:
        # Success: return the conversation thread ID
        return jsonify({'thread_id': thread_id})
    else:
        # Failure: return an error (500 = Internal Server Error)
        return jsonify({'error': 'Failed to process request'}), 500


@app.route('/ask', methods=['POST'])
def ask():
    """
    WHAT THIS ENDPOINT DOES:
    This is the main endpoint where users send their questions to the AI assistant. 
    It handles rate limiting (preventing too many questions per day) and then 
    forwards the question to the AI for processing.
    
    URL: POST /ask
    REQUIRES:
    - user-token in the request headers (for authentication)
    - prompt (the user's question), thread_id (conversation ID), and objectId in the request body
    
    RETURNS: A streaming response with the AI's answer in JSON format
    """
    
    # STEP 1: Check authentication - make sure the user is logged in
    user_token = request.headers.get('user-token')  # Get the authentication token from headers
    if not user_token:
        # If no token provided, return an error (401 = Unauthorized)
        return jsonify({'error': 'User token is missing'}), 401

    # STEP 2: Get and validate the request data
    data = request.get_json()  # Get the JSON data from the request body
    if not data:
        # If no data provided, return an error (400 = Bad Request)
        return jsonify({'error': 'Request body is missing'}), 400
    
    # STEP 3: Extract the required information from the request
    prompt = data.get('prompt')        # The user's question/message
    thread_id = data.get('thread_id')  # The conversation ID
    object_id = data.get('objectId')   # The user's unique ID
    
    # STEP 4: Make sure all required information is provided
    if not prompt or not thread_id or not object_id:
        return jsonify({'error': 'prompt, thread_id, and objectId are required'}), 400

    # STEP 5: Set up connection to user database for rate limiting
    base_url = "https://toughquilt.backendless.app/api"
    headers = {'user-token': user_token, 'Content-Type': 'application/json'}
    
    try:
        # STEP 6: Get the user's current data to check their daily usage
        user_url = f"{base_url}/data/Users/{object_id}"
        user_response = httpx.get(user_url, headers=headers)
        user_response.raise_for_status()  # Check if the request was successful
        user_data = user_response.json()  # Convert response to Python dictionary

        # STEP 7: Check if the user has reached their daily limit
        daily_count = user_data.get('dailyQuestionCount', 0)  # Get current count, default to 0
        if daily_count >= 100:
            # User has reached their daily limit (429 = Too Many Requests)
            return jsonify({'error': 'Daily limit reached'}), 429

        # STEP 8: Update the user's question count in the database
        new_count = daily_count + 1  # Increment the count
        update_payload = {'dailyQuestionCount': new_count}  # Data to update
        update_response = httpx.put(user_url, json=update_payload, headers=headers)
        update_response.raise_for_status()  # Check if the update was successful

        # STEP 9: Send the question to the AI and return the streaming response
        # This uses the generate_structured_response function we defined earlier
        # stream_with_context allows the response to be sent back in chunks as it's generated
        return Response(stream_with_context(generate_structured_response(thread_id, prompt)), mimetype='application/json')

    except Exception as e:
        # If anything goes wrong, log the error and return a generic error message
        print(f"Error in ask endpoint: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/reset_thread', methods=['POST'])
def reset_thread():
    """
    WHAT THIS ENDPOINT DOES:
    This endpoint allows users to completely reset their conversation with the AI.
    It's like clearing the chat history and starting fresh. The old conversation
    is deleted and the user can begin a new conversation from scratch.
    
    URL: POST /reset_thread
    REQUIRES:
    - user-token in the request headers (for authentication)
    - objectId in the request body (the user's unique ID)
    
    RETURNS: Success or failure message
    """
    
    # STEP 1: Check authentication - make sure the user is logged in
    user_token = request.headers.get('user-token')  # Get the authentication token from headers
    if not user_token:
        # If no token provided, return an error (401 = Unauthorized)
        return jsonify({'error': 'User token is missing'}), 401
    
    # STEP 2: Get and validate the request data
    data = request.get_json()  # Get the JSON data from the request body
    if not data or 'objectId' not in data:
        # If no data or missing objectId, return an error (400 = Bad Request)
        return jsonify({'error': 'objectId is missing from request body'}), 400
    
    # STEP 3: Extract the user's unique ID from the request
    user_object_id = data['objectId']
    
    # STEP 4: Call the reset function to clear the user's conversation
    success = reset_user_thread(user_token, user_object_id)
    
    # STEP 5: Return the result based on whether the reset was successful
    if success:
        # Reset was successful
        return jsonify({'status': 'success', 'message': 'Thread reset successfully'})
    else:
        # Reset failed (500 = Internal Server Error)
        return jsonify({'status': 'failure', 'message': 'Failed to reset thread'}), 500


@app.route('/create-checkout-session', methods=['POST'])
def create_checkout_session():
    """
    WHAT THIS ENDPOINT DOES:
    This endpoint creates a Stripe payment session for users who want to subscribe
    to a paid plan. It's like setting up a shopping cart with the subscription
    plan they chose, then sending them to Stripe's secure payment page.
    
    URL: POST /create-checkout-session
    REQUIRES:
    - planType (either 'monthly' or 'annual') in the request body
    - objectId (the user's unique ID) in the request body
    
    RETURNS: A checkout_url where the user can complete their payment
    """
    
    # STEP 1: Get and validate the request data
    data = request.get_json()  # Get the JSON data from the request body
    if not data:
        # If no data provided, return an error (400 = Bad Request)
        return jsonify({'error': 'Request body is missing'}), 400
    
    # STEP 2: Extract the required information from the request
    plan_type = data.get('planType')      # Which subscription plan they want
    user_object_id = data.get('objectId') # The user's unique ID
    
    # STEP 3: Make sure all required information is provided
    if not plan_type or not user_object_id:
        return jsonify({'error': 'planType and objectId are required'}), 400

    # STEP 4: Define the Stripe price IDs for each subscription plan
    # These IDs are created in the Stripe dashboard and link to specific pricing
    price_ids = {
        'monthly': 'price_1Rl2mc2Lfw5u3Q4QuJGFFgiG',  # Monthly subscription price ID
        'annual': 'price_1Rl2pB2Lfw5u3Q4QFpW9Olha'    # Annual subscription price ID
    }

    # STEP 5: Validate that the requested plan type exists
    if plan_type not in price_ids:
        return jsonify({'error': 'Invalid plan_type'}), 400

    try:
        # STEP 6: Create a Stripe checkout session
        # This sets up a secure payment page on Stripe's servers
        session = stripe.checkout.Session.create(
            mode='subscription',  # This is a recurring subscription, not a one-time payment
            success_url='https://acqadvantage.com/?payment=success',  # Where to send user after successful payment
            cancel_url='https://acqadvantage.com/?page=home',         # Where to send user if they cancel
            client_reference_id=user_object_id,  # Our internal user ID (so we know who paid)
            line_items=[{
                'price': price_ids[plan_type],  # Which price/plan they're buying
                'quantity': 1                   # How many (always 1 for subscriptions)
            }]
        )
        
        # STEP 7: Return the checkout URL where the user can complete payment
        return jsonify({'checkout_url': session.url})
        
    except Exception as e:
        # If anything goes wrong with Stripe, log the error and return a generic error
        print(f"Error creating Stripe session: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/verify-payment-session', methods=['POST'])
def verify_payment_session():
    """
    WHAT THIS ENDPOINT DOES:
    After a user completes payment on Stripe, this endpoint verifies that the payment
    was actually successful and then activates their subscription in our database.
    It's like double-checking that the payment went through before giving them access.
    
    URL: POST /verify-payment-session
    REQUIRES:
    - session_id (the Stripe session ID from the completed payment) in the request body
    
    RETURNS: Success confirmation if payment is verified and subscription is activated
    """
    
    # STEP 1: Get and validate the request data
    data = request.get_json()  # Get the JSON data from the request body
    if not data:
        # If no data provided, return an error (400 = Bad Request)
        return jsonify({'error': 'Request body is missing'}), 400
    
    # STEP 2: Extract the Stripe session ID from the request
    session_id = data.get('session_id')  # The ID of the completed Stripe session
    if not session_id:
        return jsonify({'error': 'session_id is required'}), 400

    try:
        # STEP 3: Get the payment session details from Stripe
        session = stripe.checkout.Session.retrieve(session_id)
        
        # STEP 4: Verify that the payment was actually completed successfully
        if session.status != 'complete' or session.payment_status != 'paid':
            # If payment wasn't successful, return an error
            return jsonify({'error': 'Payment not successful'}), 400
        
        # STEP 5: Extract important information from the successful payment session
        client_reference_id = session.client_reference_id  # Our internal user ID
        subscription_id = session.subscription              # Stripe's subscription ID
        
        # STEP 6: Make sure we have all the information we need
        if not client_reference_id or not subscription_id:
            return jsonify({'error': 'Missing client_reference_id or subscription_id in session'}), 400
        
        # STEP 7: Find the user's subscription record in our database
        base_url = "https://toughquilt.backendless.app/api"
        query_url = f"{base_url}/data/Subscriptions"
        
        # Search for subscription records that belong to this user
        query_params = {'where': f"ownerId.objectId = '{client_reference_id}'"}
        
        query_response = httpx.get(query_url, params=query_params)
        query_response.raise_for_status()  # Check if the request was successful
        subscriptions = query_response.json()  # Convert response to Python list
        
        # STEP 8: Make sure we found a subscription record for this user
        if not subscriptions:
            return jsonify({'error': f'No subscription found for user {client_reference_id}'}), 404
        
        # STEP 9: Update the subscription record to mark it as active
        subscription_object_id = subscriptions[0]['objectId']  # Get the subscription's database ID
        update_url = f"{base_url}/data/Subscriptions/{subscription_object_id}"
        
        # Prepare the data to update: mark as active and save Stripe's subscription ID
        update_payload = {
            'status': 'active',                    # Activate the subscription
            'stripeSubscriptionId': subscription_id  # Link to Stripe's subscription record
        }
        
        # Send the update to our database
        update_response = httpx.put(update_url, json=update_payload)
        update_response.raise_for_status()  # Check if the update was successful
        
        # STEP 10: Return success confirmation
        return jsonify({'status': 'success'}), 200
        
    except Exception as e:
        # If anything goes wrong, log the error and return a generic error message
        return jsonify({'error': f'Server error: {str(e)}'}), 500


@app.route('/stripe-webhook', methods=['POST'])
def stripe_webhook():
    """
    WHAT THIS ENDPOINT DOES:
    This is a "webhook" - a special endpoint that Stripe calls automatically when 
    payment events happen. It's like Stripe sending us a notification saying 
    "Hey, someone just completed a payment!" We use this to automatically 
    activate subscriptions when payments succeed.
    
    URL: POST /stripe-webhook
    CALLED BY: Stripe's servers (not by users directly)
    RECEIVES: Payment event data from Stripe
    
    RETURNS: Always returns success (even if there are errors, to avoid Stripe retrying)
    """
    
    # STEP 1: Get the raw webhook data from Stripe
    payload = request.get_data()  # Raw data from Stripe
    sig_header = request.headers.get('Stripe-Signature')  # Security signature to verify it's really from Stripe
    
    # STEP 2: Verify the webhook has a security signature
    if not sig_header:
        return jsonify({'error': 'Missing Stripe-Signature header'}), 400

    try:
        # STEP 3: Verify the webhook is really from Stripe (security check)
        # This prevents someone from faking webhook calls to our system
        event = stripe.Webhook.construct_event(
            payload, 
            sig_header, 
            os.getenv('STRIPE_WEBHOOK_SECRET')  # Secret key shared between us and Stripe
        )
    except Exception as e:
        # If verification fails, reject the webhook
        return jsonify({'error': str(e)}), 400

    # STEP 4: Check if this is a payment completion event
    if event['type'] == 'checkout.session.completed':
        # Extract the payment session data
        session = event['data']['object']
        client_reference_id = session.get('client_reference_id')  # Our internal user ID
        subscription_id = session.get('subscription')             # Stripe's subscription ID
        
        # STEP 5: Make sure we have the information we need
        if not client_reference_id or not subscription_id:
            # If missing information, just return success (don't retry)
            return jsonify({'status': 'success'}), 200
        
        # STEP 6: Find and update the user's subscription in our database
        base_url = "https://toughquilt.backendless.app/api"
        query_url = f"{base_url}/data/Subscriptions"
        
        # Search for subscription records that belong to this user
        query_params = {'where': f"ownerId.objectId = '{client_reference_id}'"}
        
        try:
            # STEP 7: Get the user's subscription record
            query_response = httpx.get(query_url, params=query_params)
            query_response.raise_for_status()
            subscriptions = query_response.json()
            
            # STEP 8: If no subscription found, just return success (no error)
            if not subscriptions:
                return jsonify({'status': 'success'}), 200
            
            # STEP 9: Update the subscription to mark it as active
            subscription_object_id = subscriptions[0]['objectId']
            update_url = f"{base_url}/data/Subscriptions/{subscription_object_id}"
            
            # Prepare the update: activate subscription and link to Stripe
            update_payload = {
                'status': 'active',                    # Activate the subscription
                'stripeSubscriptionId': subscription_id  # Link to Stripe's subscription record
            }
            
            # Send the update to our database
            update_response = httpx.put(update_url, json=update_payload)
            update_response.raise_for_status()
            
        except Exception as e:
            # If anything goes wrong, log it but still return success
            # (We don't want Stripe to keep retrying the webhook)
            print(f"Error in webhook updating subscription: {e}")
            return jsonify({'status': 'success'}), 200
    
    # STEP 10: Always return success to Stripe (prevents unnecessary retries)
    return jsonify({'status': 'success'}), 200


@app.route('/test-openai')
def test_openai_connection():
    """
    WHAT THIS ENDPOINT DOES:
    This is a diagnostic endpoint used to test if our connection to OpenAI is working properly.
    It's like doing a "ping" test to make sure we can communicate with OpenAI's servers.
    This is helpful for troubleshooting connection issues.
    
    URL: GET /test-openai
    REQUIRES: Nothing (no authentication needed for this test)
    
    RETURNS: Success or failure message indicating if OpenAI connection is working
    """
    try:
        print("DEBUG: Testing OpenAI connection...")
        
        # STEP 1: Make a simple, low-cost API call to OpenAI
        # We use models.list() because it's a lightweight operation that doesn't cost much
        openai_client.models.list()
        
        print("DEBUG: Successfully connected to OpenAI.")
        
        # STEP 2: Return success message
        return jsonify({
            "status": "success", 
            "message": "Connection to OpenAI API successful."
        })
        
    except Exception as e:
        # STEP 3: If connection fails, log the error and return failure message
        print(f"DEBUG: Failed to connect to OpenAI. Error: {e}")
        return jsonify({
            "status": "failed", 
            "error": str(e)
        }), 500


@app.route("/decision-table/<sheet>", methods=["GET"])
def decision_table(sheet):
    try:
        df = pd.read_excel("Contract Award Decision Tree.xlsx", sheet_name=sheet)
        return jsonify(df.to_dict(orient="records"))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# === MAIN EXECUTION ===
# This section runs when the script is executed directly (not imported as a module)
if __name__ == '__main__':
    # Start the Flask web server
    # debug=True means it will automatically restart when code changes are made
    # and provide detailed error messages for development
    app.run(debug=True)
