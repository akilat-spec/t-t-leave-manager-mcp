# Secure Leave Manager MCP Server

A secure MCP server for employee management with API key authentication.

## Security Features

- 🔐 API Key Authentication (Bearer token, X-API-Key header, or query parameter)
- 🛡️ Middleware-based protection for all MCP tools
- 🔒 Environment-based configuration
- 👤 Non-root Docker user for security

## Setup Instructions

### 1. Generate API Keys

```bash
# Generate a secure API key
openssl rand -hex 32