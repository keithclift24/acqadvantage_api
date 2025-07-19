# AcqAdvantage API

This is a Flask-based API that serves as a backend for the AcqAdvantage application. It integrates with OpenAI's Assistants API to provide chat functionality and uses Stripe for managing subscriptions. User data and chat threads are managed through a Backendless backend.

## Features

- **Chat Interface**: Provides endpoints to start, manage, and interact with an OpenAI Assistant.
- **User Management**: Connects to a Backendless database to manage user data, including chat history and daily question limits.
- **Subscription Handling**: Integrates with Stripe for creating and verifying checkout sessions for monthly and annual subscription plans.
- **Webhook Support**: Includes a webhook endpoint to handle events from Stripe, such as `checkout.session.completed`.

## Project Structure

```
.
├── .gitignore
├── app.py              # Main Flask application file
├── README.md           # This file
├── requirements.txt    # Python dependencies
└── test_api.http       # HTTP requests for testing the API
```

## Setup and Installation

### 1. Clone the repository

```bash
git clone https://github.com/keithclift24/acqadvantage_api.git
cd acqadvantage_api
```

### 2. Create a virtual environment and install dependencies

It is recommended to use a virtual environment to manage the project's dependencies.

```bash
# For Windows
python -m venv venv
venv\Scripts\activate

# For macOS/Linux
python3 -m venv venv
source venv/bin/activate
```

Install the required packages using pip:

```bash
pip install -r requirements.txt
```

### 3. Configure Environment Variables

Create a `.env` file in the root of the project and add the following environment variables:

```
OPENAI_API_KEY='your_openai_api_key'
STRIPE_SECRET_KEY='your_stripe_secret_key'
STRIPE_WEBHOOK_SECRET='your_stripe_webhook_secret'
```

- `OPENAI_API_KEY`: Your API key for the OpenAI service.
- `STRIPE_SECRET_KEY`: Your secret key for the Stripe API.
- `STRIPE_WEBHOOK_SECRET`: Your webhook signing secret from Stripe.

## Running the Application

To run the Flask development server, use the following command:

```bash
python app.py
```

The API will be available at `http://127.0.0.1:5000`.

## API Endpoints

### Health Check

- **GET /**: Checks if the API is running.

### Chat

- **POST /start_chat**: Initializes a new chat session for a user.
- **POST /ask**: Sends a user's prompt to the OpenAI Assistant and streams the response.
- **POST /reset_thread**: Resets the current chat thread for a user.

### Payments (Stripe)

- **POST /create-checkout-session**: Creates a Stripe checkout session for a subscription plan.
- **POST /verify-payment-session**: Verifies the status of a payment session.
- **POST /stripe-webhook**: Handles incoming webhooks from Stripe to update subscription statuses.

## Testing

You can use the `test_api.http` file with a REST client (like the VS Code REST Client extension) to test the API endpoints.
