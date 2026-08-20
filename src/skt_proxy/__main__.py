"""
Entrypoint for running skt-proxy as a module: `python -m skt_proxy`
"""

import sys
from skt_proxy.app import app

def main():
    port = 5000
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        port = int(sys.argv[1])
    app.run(host="0.0.0.0", port=port, debug=False)

if __name__ == "__main__":
    main()
