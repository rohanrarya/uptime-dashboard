# =============================================================================
# Local Development Runner
# Run this to start the dashboard locally:
# python run.py
# =============================================================================

import os
import sys

# Add api directory to sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'api'))
from index import app

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"\n=======================================================")
    print(f"🚀 Ops Command Center running at: http://127.0.0.1:{port}")
    print(f"=======================================================\n")
    app.run(host='0.0.0.0', port=port, debug=True)
