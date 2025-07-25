# AcqAdvantage API - Complete Guide for Non-Coders

## What is This Project?

This is a **web API** (Application Programming Interface) for AcqAdvantage - a service that provides AI-powered assistance. Think of it as the "behind-the-scenes engine" that powers a website or mobile app where users can ask questions to an AI assistant and manage their subscriptions.

### Simple Analogy
Imagine this API as the kitchen of a restaurant:
- **The kitchen (this API)** prepares the food (processes requests)
- **The waiters (frontend applications)** take orders from customers and bring back the prepared food
- **The customers (end users)** never see the kitchen directly, but they get the results of what happens in the kitchen

## What Does This API Do?

This API provides several key services:

1. **🤖 AI Chat Service**: Users can have conversations with an AI assistant
2. **💳 Payment Processing**: Handles subscription payments through Stripe
3. **👤 User Management**: Manages user accounts and conversation history
4. **📊 Usage Tracking**: Tracks daily usage limits and subscription status

## Project Structure (What Each File Does)

```
acqadvantage_api/
├── app.py              # Main application code (the "brain" of the API)
├── requirements.txt    # List of all libraries needed to run the app
├── README.md          # This documentation file
├── .gitignore         # Tells Git which files to ignore
└── .env               # Secret keys and configuration (not included in Git)
```

### File Explanations

- **`app.py`**: The main Python file that contains all the code for the API. It's heavily commented to explain what each part does.
- **`requirements.txt`**: Lists all the external libraries (like tools and ingredients) that the application needs to work properly.
- **`README.md`**: This documentation file that explains everything for non-coders.
- **`.env`**: Contains secret keys and passwords (like OpenAI API keys, Stripe keys, etc.). This file is not shared publicly for security reasons.

## How the API Works (Step by Step)

### 1. User Starts a Chat
```
User → Frontend App → API (/start_chat) → Creates/Gets Conversation → Returns Chat ID
```

### 2. User Asks a Question
```
User → Frontend App → API (/ask) → Checks Limits → Sends to OpenAI → Returns AI Response
```

### 3. User Subscribes to Paid Plan
```
User → Frontend App → API (/create-checkout-session) → Stripe Payment Page → Payment Success → API Updates Database
```

## API Endpoints (What Each URL Does)

Think of endpoints as different "services" or "windows" at a business:

| Endpoint | What It Does | Like Going To... |
|----------|--------------|------------------|
| `GET /` | Health check - "Is the API running?" | Reception desk to ask "Are you open?" |
| `POST /start_chat` | Initialize conversation with AI | Customer service to start a new inquiry |
| `POST /ask` | Send question to AI assistant | The main service counter with your question |
| `POST /reset_thread` | Clear chat history and start fresh | Asking to "start over" with a clean slate |
| `POST /create-checkout-session` | Create payment session | Going to the cashier to pay |
| `POST /verify-payment-session` | Confirm payment was successful | Cashier verifying your payment went through |
| `POST /stripe-webhook` | Stripe notifies us of payment events | Bank calling to confirm a transaction |
| `GET /test-openai` | Test if AI connection is working | Testing if the phone line to headquarters works |

## External Services Used

This API connects to several external services:

### 🧠 OpenAI
- **What it does**: Provides the AI assistant that answers user questions
- **Why we need it**: This is the "brain" that generates intelligent responses
- **How it works**: We send user questions to OpenAI, they send back AI-generated answers

### 💳 Stripe
- **What it does**: Handles all payment processing and subscription management
- **Why we need it**: We can't process credit cards ourselves safely - Stripe handles this securely
- **How it works**: Users pay through Stripe's secure system, Stripe tells us when payments succeed

### 🗄️ Backendless
- **What it does**: Acts as our database to store user information, conversation history, and subscription status
- **Why we need it**: We need somewhere to remember user information between visits
- **How it works**: We save and retrieve user data through Backendless's API

## Key Features Explained

### 🔒 Authentication
- Every request (except health checks) requires a `user-token` in the headers
- This is like showing your membership card before getting service
- Prevents unauthorized people from using the service

### 📊 Rate Limiting
- Users can ask a maximum of 100 questions per day
- This prevents abuse and controls costs
- The counter resets daily (managed by the frontend/database)

### 💬 Conversation Threading
- Each user has an ongoing "conversation" with the AI (called a "thread")
- The AI remembers previous messages in the conversation
- Users can reset their conversation to start fresh

### 🔄 Streaming Responses
- AI responses are sent back as they're generated (streaming)
- This makes the user experience feel faster and more responsive
- Like watching someone type a message in real-time

## Security Features

### 🛡️ CORS (Cross-Origin Resource Sharing)
- Allows websites from different domains to safely use our API
- Prevents malicious websites from making unauthorized requests

### 🔐 Environment Variables
- All secret keys (API keys, passwords) are stored in environment variables
- Never hardcoded in the source code for security
- Loaded from a `.env` file that's not shared publicly

### ✅ Input Validation
- All user inputs are validated before processing
- Prevents malicious or malformed data from causing problems
- Returns clear error messages for invalid requests

### 🏪 Webhook Security
- Stripe webhooks are verified using cryptographic signatures
- Ensures webhook calls are really from Stripe, not from attackers

## Error Handling

The API uses standard HTTP status codes:

- **200**: Success - Everything worked perfectly
- **400**: Bad Request - User sent invalid data
- **401**: Unauthorized - Missing or invalid authentication
- **404**: Not Found - Requested resource doesn't exist
- **429**: Too Many Requests - Rate limit exceeded
- **500**: Internal Server Error - Something went wrong on our end

## Environment Setup (For Developers)

### Required Environment Variables (.env file)
```bash
# OpenAI Configuration
OPENAI_API_KEY=your_openai_api_key_here

# Stripe Configuration
STRIPE_SECRET_KEY=your_stripe_secret_key_here
STRIPE_WEBHOOK_SECRET=your_stripe_webhook_secret_here

# These would be loaded from environment variables
# The actual values are kept secret for security
```

### Installation Steps
1. **Install Python** (version 3.8 or higher)
2. **Install dependencies**: `pip install -r requirements.txt`
3. **Create .env file** with your secret keys
4. **Run the application**: `python app.py`

## Testing the API

### Health Check
```bash
curl http://localhost:5000/
# Should return: {"status": "API is running"}
```

### Test OpenAI Connection
```bash
curl http://localhost:5000/test-openai
# Should return: {"status": "success", "message": "Connection to OpenAI API successful."}
```

## Common Issues and Solutions

### 🚫 "User token is missing" Error
- **Problem**: Request doesn't include authentication
- **Solution**: Add `user-token` header to your request

### 🚫 "Daily limit reached" Error
- **Problem**: User has asked 100+ questions today
- **Solution**: Wait until tomorrow or upgrade to paid plan

### 🚫 "Failed to extract valid JSON" Error
- **Problem**: OpenAI's response wasn't in expected format
- **Solution**: This usually resolves itself on retry; might indicate AI assistant configuration issue

### 🚫 "Payment not successful" Error
- **Problem**: Stripe payment didn't complete properly
- **Solution**: Check Stripe dashboard for payment status; user may need to retry payment

## For Business Stakeholders

### 💰 Cost Structure
- **OpenAI**: Pay per AI conversation (usage-based)
- **Stripe**: Small percentage per transaction + monthly fee
- **Backendless**: Based on database usage and API calls
- **Hosting**: Depends on traffic volume

### 📈 Scalability
- Built with Flask (Python) - can handle moderate traffic
- Uses streaming responses for better user experience
- Database operations are optimized for performance
- Can be easily deployed to cloud platforms

### 🔧 Maintenance Requirements
- Monitor API key usage and costs
- Keep dependencies updated for security
- Monitor error rates and performance
- Regular backups of user data

## Development Best Practices Used

### 📝 Code Documentation
- Every function has detailed comments explaining what it does
- Step-by-step comments for complex operations
- Clear variable names that explain their purpose

### 🧪 Error Handling
- Try-catch blocks around all external API calls
- Graceful degradation when services are unavailable
- Detailed logging for debugging issues

### 🏗️ Code Organization
- Clear separation between different types of functionality
- Logical grouping of related functions
- Consistent naming conventions throughout

## Future Enhancement Possibilities

### 🚀 Performance Improvements
- Add caching for frequently asked questions
- Implement connection pooling for database operations
- Add load balancing for high traffic

### 🔒 Security Enhancements
- Add API rate limiting per user
- Implement request logging and monitoring
- Add additional authentication methods

### 📊 Analytics and Monitoring
- Add detailed usage analytics
- Implement performance monitoring
- Add alerts for system issues

### 🎯 Feature Additions
- Support for file uploads in conversations
- Multi-language support
- Advanced user management features

---

## Summary

This AcqAdvantage API is a well-structured, secure, and scalable solution for providing AI-powered chat services with integrated payment processing. The code is thoroughly documented to ensure maintainability and understanding by both technical and non-technical stakeholders.

The system is designed with security, reliability, and user experience as top priorities, making it suitable for production use while remaining simple enough for easy maintenance and future enhancements.
