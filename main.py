import os
import re
import urllib.parse
import secrets
from typing import List, Optional, Dict, Any
from difflib import SequenceMatcher
from datetime import datetime, date, timedelta

# third-party
import mysql.connector
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import PlainTextResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware import Middleware

# optional Levenshtein import
try:
    import Levenshtein
except Exception:
    Levenshtein = None

# -------------------------------
# Configuration
# -------------------------------
REQUIRE_API_KEY = os.environ.get("REQUIRE_API_KEY", "true").lower() == "true"
VALID_API_KEYS = set(os.environ.get("API_KEYS", "").split(",")) if REQUIRE_API_KEY else set()
DEBUG = os.environ.get("DEBUG", "false").lower() == "true"

# -------------------------------
# API Key Authentication Middleware
# -------------------------------
class APIKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Skip auth for health check and root endpoint
        if request.url.path in ["/health", "/"]:
            return await call_next(request)
        
        # Skip auth if not required
        if not REQUIRE_API_KEY:
            return await call_next(request)
        
        # Extract API key from headers or query parameters
        api_key = None
        
        # Check Authorization header
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            api_key = auth_header.replace("Bearer ", "")
        
        # Check X-API-Key header
        if not api_key:
            api_key = request.headers.get("X-API-Key")
        
        # Check query parameter
        if not api_key:
            api_key = request.query_params.get("api_key")
        
        # Validate API key
        if not api_key:
            return JSONResponse(
                status_code=401,
                content={
                    "error": "API key required",
                    "message": "Provide API key via Authorization: Bearer <key>, X-API-Key header, or api_key query parameter"
                }
            )
        
        if api_key not in VALID_API_KEYS:
            return JSONResponse(
                status_code=403,
                content={
                    "error": "Invalid API key",
                    "message": "The provided API key is not valid"
                }
            )
        
        # Add API key to request state for logging/audit
        request.state.api_key = api_key
        
        return await call_next(request)

# -------------------------------
# MCP server with middleware
# -------------------------------
mcp = FastMCP(
    "LeaveManagerPlus",
    middleware=[Middleware(APIKeyMiddleware)]
)

# -------------------------------
# MySQL connection (reads from env)
# -------------------------------
def get_connection():
    """
    Read DB credentials from environment variables
    """
    return mysql.connector.connect(
        host=os.environ.get("DB_HOST", "103.174.10.72"),
        user=os.environ.get("DB_USER", "tt_crm_mcp"),
        password=os.environ.get("DB_PASSWORD", "F*PAtqhu@sg2w58n"),
        database=os.environ.get("DB_NAME", "tt_crm_mcp"),
        port=int(os.environ.get("DB_PORT", "3306")),
        autocommit=True,
    )

# -------------------------------
# AI-Powered Name Matching Utilities
# -------------------------------
class NameMatcher:
    @staticmethod
    def normalize_name(name: str) -> str:
        name = (name or "").lower().strip()
        name = re.sub(r'[^\w\s]', '', name)
        name = re.sub(r'\s+', ' ', name)
        return name

    @staticmethod
    def similarity_score(name1: str, name2: str) -> float:
        name1_norm = NameMatcher.normalize_name(name1)
        name2_norm = NameMatcher.normalize_name(name2)

        if Levenshtein:
            try:
                dist = Levenshtein.distance(name1_norm, name2_norm)
                levenshtein_sim = 1 - (dist / max(len(name1_norm), len(name2_norm), 1))
            except Exception:
                levenshtein_sim = SequenceMatcher(None, name1_norm, name2_norm).ratio()
        else:
            levenshtein_sim = SequenceMatcher(None, name1_norm, name2_norm).ratio()

        sequence_sim = SequenceMatcher(None, name1_norm, name2_norm).ratio()
        combined_score = (levenshtein_sim * 0.6) + (sequence_sim * 0.4)
        return combined_score

    @staticmethod
    def extract_name_parts(full_name: str) -> Dict[str, str]:
        parts = (full_name or "").split()
        if len(parts) == 0:
            return {'first': '', 'last': ''}
        if len(parts) == 1:
            return {'first': parts[0], 'last': ''}
        elif len(parts) == 2:
            return {'first': parts[0], 'last': parts[1]}
        else:
            return {'first': parts[0], 'last': parts[-1]}

    @staticmethod
    def fuzzy_match_employee(search_name: str, employees: List[Dict[str, Any]], threshold: float = 0.6) -> List[Dict[str, Any]]:
        matches = []
        search_parts = NameMatcher.extract_name_parts(search_name)

        for emp in employees:
            scores = []
            emp_full_name = f"{emp.get('developer_name','')}".strip()
            scores.append(NameMatcher.similarity_score(search_name, emp_full_name))

            if ' ' in emp_full_name:
                first_name = emp_full_name.split()[0]
                last_name = ' '.join(emp_full_name.split()[1:])
                scores.append(NameMatcher.similarity_score(search_name, f"{first_name} {last_name}"))
                scores.append(NameMatcher.similarity_score(search_name, f"{last_name} {first_name}"))

            if search_parts['last']:
                first_score = NameMatcher.similarity_score(search_parts['first'], emp_full_name.split()[0] if emp_full_name else '')
                last_score = NameMatcher.similarity_score(search_parts['last'], ' '.join(emp_full_name.split()[1:]) if ' ' in emp_full_name else '')
                if first_score > 0 or last_score > 0:
                    scores.append((first_score + last_score) / 2)

            best_score = max(scores) if scores else 0
            if best_score >= threshold:
                matches.append({'employee': emp, 'score': best_score, 'match_type': 'fuzzy'})

        matches.sort(key=lambda x: x['score'], reverse=True)
        return matches

# -------------------------------
# Enhanced Employee Search with AI
# -------------------------------
def fetch_employees_ai(search_term: str = None, emp_id: int = None) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        if emp_id:
            cursor.execute("""
                SELECT d.id, d.developer_name, d.designation, d.email_id, d.mobile, 
                       d.status, d.doj, d.emp_number, d.blood_group,
                       u.username, d.opening_leave_balance, d.is_pf_enabled, d.pf_join_date
                FROM developer d
                LEFT JOIN user u ON d.user_id = u.user_id
                WHERE d.id = %s
            """, (emp_id,))
        elif search_term:
            cursor.execute("""
                SELECT d.id, d.developer_name, d.designation, d.email_id, d.mobile, 
                       d.status, d.doj, d.emp_number, d.blood_group,
                       u.username, d.opening_leave_balance, d.is_pf_enabled, d.pf_join_date
                FROM developer d
                LEFT JOIN user u ON d.user_id = u.user_id
                WHERE d.developer_name LIKE %s OR d.email_id LIKE %s 
                   OR d.mobile LIKE %s OR d.emp_number LIKE %s
                ORDER BY d.developer_name
            """, (f"%{search_term}%", f"%{search_term}%", f"%{search_term}%", f"%{search_term}%"))
        else:
            return []

        rows = cursor.fetchall()

        if search_term and not rows:
            # fallback fuzzy search among active employees
            cursor.execute("""
                SELECT d.id, d.developer_name, d.designation, d.email_id, d.mobile, 
                       d.status, d.doj, d.emp_number, d.blood_group,
                       u.username, d.opening_leave_balance, d.is_pf_enabled, d.pf_join_date
                FROM developer d
                LEFT JOIN user u ON d.user_id = u.user_id
                WHERE d.status = 1
            """)
            all_employees = cursor.fetchall()
            fuzzy_matches = NameMatcher.fuzzy_match_employee(search_term, all_employees)
            rows = [match['employee'] for match in fuzzy_matches[:5]]

        return rows

    except Exception as e:
        if DEBUG:
            print(f"Database error: {e}")
        return []
    finally:
        cursor.close()
        conn.close()

# -------------------------------
# Leave Management Functions
# -------------------------------
def get_leave_balance_for_employee(developer_id: int) -> Dict[str, Any]:
    """Calculate leave balance for an employee"""
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT opening_leave_balance, doj, status 
            FROM developer 
            WHERE id = %s
        """, (developer_id,))
        developer_info = cursor.fetchone()
        
        if not developer_info:
            return {"error": "Employee not found"}
        
        cursor.execute("""
            SELECT leave_type, COUNT(*) as count
            FROM leave_requests 
            WHERE developer_id = %s AND status = 'Approved'
            GROUP BY leave_type
        """, (developer_id,))
        leave_counts = cursor.fetchall()
        
        used_leaves = 0.0
        for leave in leave_counts:
            lt = (leave.get('leave_type') or '').upper()
            cnt = float(leave.get('count') or 0)
            if lt == 'FULL DAY':
                used_leaves += cnt
            elif lt in ['HALF DAY', 'COMPENSATION HALF DAY']:
                used_leaves += cnt * 0.5
            elif lt in ['2 HRS', 'COMPENSATION 2 HRS']:
                used_leaves += cnt * 0.25
            else:
                # default treat as full day for unknown types
                used_leaves += cnt

        opening_balance = float(developer_info.get('opening_leave_balance') or 0)
        current_balance = opening_balance - used_leaves
        
        return {
            "opening_balance": opening_balance,
            "used_leaves": used_leaves,
            "current_balance": current_balance,
            "leave_details": leave_counts
        }
        
    except Exception as e:
        return {"error": f"Error calculating leave balance: {str(e)}"}
    finally:
        cursor.close()
        conn.close()

def get_employee_work_report(developer_id: int, days: int = 30) -> List[Dict[str, Any]]:
    """Get recent work reports for an employee"""
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT wr.task, wr.description, wr.date, wr.total_time, 
                   p.title as project_name, c.client_name
            FROM work_report wr
            LEFT JOIN project p ON wr.project_id = p.id
            LEFT JOIN client c ON wr.client_id = c.id
            WHERE wr.developer_id = %s 
            AND wr.date >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
            ORDER BY wr.date DESC
            LIMIT 100
        """, (developer_id, days))
        
        return cursor.fetchall()
        
    except Exception as e:
        if DEBUG:
            print(f"Error fetching work report: {e}")
        return []
    finally:
        cursor.close()
        conn.close()

def get_employee_leave_requests(developer_id: int, limit: int = 100) -> List[Dict[str, Any]]:
    """Get leave requests for an employee"""
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT request_id, leave_type, date_of_leave, status, 
                   dev_comments, admin_comments, created_at
            FROM leave_requests 
            WHERE developer_id = %s 
            ORDER BY date_of_leave DESC
            LIMIT %s
        """, (developer_id, limit))
        
        return cursor.fetchall()
        
    except Exception as e:
        if DEBUG:
            print(f"Error fetching leave requests: {e}")
        return []
    finally:
        cursor.close()
        conn.close()

# -------------------------------
# Employee Formatting and Resolution
# -------------------------------
def format_employee_options(employees: List[Dict[str, Any]]) -> str:
    options = []
    for i, emp in enumerate(employees, 1):
        option = f"{i}. 👤 {emp.get('developer_name','Unknown')}"
        if emp.get('designation'):
            option += f" | 💼 {emp.get('designation')}"
        if emp.get('email_id'):
            option += f" | 📧 {emp.get('email_id')}"
        if emp.get('emp_number'):
            option += f" | 🆔 {emp.get('emp_number')}"
        if emp.get('mobile'):
            option += f" | 📞 {emp.get('mobile')}"
        status = "Active" if emp.get('status') == 1 else "Inactive"
        option += f" | 🔰 {status}"
        options.append(option)
    return "\n".join(options)

def resolve_employee_ai(search_name: str, additional_context: str = None) -> Dict[str, Any]:
    employees = fetch_employees_ai(search_term=search_name)

    if not employees:
        return {'status': 'not_found', 'message': f"No employees found matching '{search_name}'"}

    if len(employees) == 1:
        return {'status': 'resolved', 'employee': employees[0]}

    if additional_context:
        context_lower = (additional_context or '').lower()
        filtered_employees = []
        for emp in employees:
            designation = (emp.get('designation') or '').lower()
            email = (emp.get('email_id') or '').lower()
            emp_number = (emp.get('emp_number') or '').lower()
            
            if (context_lower in designation or 
                context_lower in email or 
                context_lower in emp_number or
                context_lower in emp.get('developer_name', '').lower()):
                filtered_employees.append(emp)
        
        if len(filtered_employees) == 1:
            return {'status': 'resolved', 'employee': filtered_employees[0]}

    return {
        'status': 'ambiguous',
        'employees': employees,
        'message': f"Found {len(employees)} employees. Please specify:"
    }

# -------------------------------
# API Key Management Tools
# -------------------------------
@mcp.tool()
def generate_api_key() -> str:
    """Generate a new secure API key for authentication"""
    if not REQUIRE_API_KEY:
        return "⚠️ API key authentication is currently disabled. Set REQUIRE_API_KEY=true to enable."
    
    new_key = secrets.token_hex(32)
    return f"🔐 **New API Key Generated**\n\n`{new_key}`\n\n⚠️ **Important:**\n- Save this key securely - it cannot be recovered\n- Add it to your API_KEYS environment variable\n- Share only with authorized users\n- Keys are comma-separated in API_KEYS env var"

@mcp.tool()
def check_auth_status() -> str:
    """Check current authentication configuration"""
    status = "🔐 **Authentication Status**\n\n"
    status += f"API Key Required: {'✅ Yes' if REQUIRE_API_KEY else '❌ No'}\n"
    
    if REQUIRE_API_KEY:
        key_count = len(VALID_API_KEYS)
        status += f"Configured API Keys: {key_count}\n"
        if key_count == 0:
            status += "⚠️ Warning: No API keys configured but authentication is required!\n"
    
    status += f"\n**Usage:**\n"
    status += "- Header: `Authorization: Bearer <api_key>`\n"
    status += "- Header: `X-API-Key: <api_key>`\n"
    status += "- Query: `?api_key=<api_key>`\n"
    
    return status

# -------------------------------
# Existing HR Tools (with authentication)
# -------------------------------
@mcp.tool()
def get_employee_details(name: str, additional_context: Optional[str] = None) -> str:
    """Get comprehensive details for an employee including personal info, leave balance, and recent activity"""
    resolution = resolve_employee_ai(name, additional_context)
    
    if resolution['status'] == 'not_found':
        return f"❌ No employee found matching '{name}'."
    
    if resolution['status'] == 'ambiguous':
        options_text = format_employee_options(resolution['employees'])
        return f"🔍 {resolution['message']}\n\n{options_text}\n\n💡 Tip: You can specify by:\n- Designation (e.g., 'Developer')\n- Email\n- Employee number\n- Or say the number (e.g., '1')"

    emp = resolution['employee']
    
    # Get additional information
    leave_balance = get_leave_balance_for_employee(emp['id'])
    work_reports = get_employee_work_report(emp['id'], days=7)
    leave_requests = get_employee_leave_requests(emp['id'], limit=10)
    
    response = f"✅ **Employee Details**\n\n"
    response += f"👤 **{emp['developer_name']}**\n"
    response += f"🆔 Employee ID: {emp['id']} | Employee #: {emp.get('emp_number', 'N/A')}\n"
    response += f"💼 Designation: {emp.get('designation', 'N/A')}\n"
    response += f"📧 Email: {emp.get('email_id', 'N/A')}\n"
    response += f"📞 Mobile: {emp.get('mobile', 'N/A')}\n"
    response += f"🩸 Blood Group: {emp.get('blood_group', 'N/A')}\n"
    response += f"📅 Date of Joining: {emp.get('doj', 'N/A')}\n"
    response += f"🔰 Status: {'Active' if emp.get('status') == 1 else 'Inactive'}\n\n"
    
    # Leave Balance
    if 'error' not in leave_balance:
        response += f"📊 **Leave Balance:** {leave_balance['current_balance']:.1f} days\n"
        response += f"   - Opening Balance: {leave_balance['opening_balance']}\n"
        response += f"   - Leaves Used: {leave_balance['used_leaves']:.1f} days\n\n"
    else:
        response += f"📊 Leave Balance: Data not available\n\n"
    
    # Recent Work Reports
    if work_reports:
        response += f"📋 **Recent Work (Last 7 days):**\n"
        for report in work_reports[:3]:
            hours = (report['total_time'] or 0) / 3600 if report.get('total_time') else 0
            response += f"   - {report['date']}: {report['task'][:60]}... ({hours:.1f}h)\n"
        response += "\n"
    
    # Recent Leave Requests
    if leave_requests:
        response += f"🏖️  **Recent Leave Requests:**\n"
        for leave in leave_requests[:3]:
            status_icon = "✅" if leave['status'] == 'Approved' else "⏳" if leave['status'] in ['Requested', 'Pending'] else "❌"
            response += f"   - {leave['date_of_leave']}: {leave['leave_type']} {status_icon}\n"
    
    return response

# ... (include all your existing tools here - they will automatically be protected by the middleware)
# get_leave_balance, get_work_report, get_leave_history, search_employees, 
# get_employee_profile, get_appraisal_feedback, get_incentives, 
# get_attendance_summary, get_pf_status, get_client_list, get_projects_overview,
# get_project_status_updates, get_payments_summary, get_fixed_expenses, get_holidays

# -------------------------------
# HTTP Endpoints
# -------------------------------
@mcp.custom_route("/mcp", methods=["POST"])
async def mcp_endpoint(request: Request):
    """MCP protocol endpoint (protected by API key)"""
    return JSONResponse({"status": "MCP server is running"})

@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> PlainTextResponse:
    """Public health check endpoint"""
    return PlainTextResponse("OK")

@mcp.custom_route("/", methods=["GET"])
async def root(request: Request) -> JSONResponse:
    """Public root endpoint with API information"""
    return JSONResponse({
        "message": "Leave Manager + HR + Company Management MCP Server",
        "status": "running",
        "version": "1.0.0",
        "authentication_required": REQUIRE_API_KEY,
        "authentication_methods": [
            "Authorization: Bearer <api_key>",
            "X-API-Key: <api_key>",
            "api_key query parameter"
        ]
    })

# -------------------------------
# Run MCP server
# -------------------------------
if __name__ == "__main__":
    if Levenshtein is None and DEBUG:
        print("Warning: python-levenshtein not installed. Fuzzy quality will be slightly lower. Install with: pip install python-Levenshtein")

    if REQUIRE_API_KEY and not VALID_API_KEYS:
        print("⚠️  WARNING: API key authentication is enabled but no valid API keys are configured!")
        print("   Set API_KEYS environment variable with comma-separated keys")
        print("   Or set REQUIRE_API_KEY=false to disable authentication")

    transport = os.environ.get("MCP_TRANSPORT", "streamable-http")
    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))

    print(f"Starting Leave Manager Plus MCP Server on {host}:{port}")
    print(f"Transport: {transport}")
    print(f"API Key Authentication: {'Enabled' if REQUIRE_API_KEY else 'Disabled'}")
    
    mcp.run(transport=transport, host=host, port=port)