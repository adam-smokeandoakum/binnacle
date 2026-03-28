#!/bin/bash
# Deploy todo-md to the dev server (Node.js, magic link auth, SQLite)
# Usage: bash deploy.sh [server_ip]

SERVER="${1:-89.117.62.130}"
SSH_KEY="${HOME}/.openclaw/workspace/.persistent/config/ssh/contabo_ed25519"
REMOTE_DIR="/opt/todo-md"

echo "Deploying todo-md to $SERVER..."

# Create dir
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "root@$SERVER" "mkdir -p $REMOTE_DIR/data"

# Copy files
scp -i "$SSH_KEY" -o StrictHostKeyChecking=no \
  server.js index.html package.json \
  "root@$SERVER:$REMOTE_DIR/"

# Install deps and set up systemd service
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "root@$SERVER" "
  cd $REMOTE_DIR
  npm install --omit=dev 2>&1 | tail -3

cat > /etc/systemd/system/todo-md.service << 'UNIT'
[Unit]
Description=Todo MD Server
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/todo-md
ExecStart=/usr/bin/node /opt/todo-md/server.js
Restart=always
RestartSec=5
Environment=PORT=8070
Environment=DB_PATH=/opt/todo-md/data/todo.db
Environment=BASE_URL=http://89.117.62.130/todo-md
Environment=SMTP_HOST=smtp.gmail.com
Environment=SMTP_PORT=587
Environment=SMTP_USER=adam@smokeandoakum.co
Environment=SMTP_PASS=gxhfybjjfwfqnabu
Environment=SMTP_FROM=Todo <adam@smokeandoakum.co>

[Install]
WantedBy=multi-user.target
UNIT

  systemctl daemon-reload
  systemctl enable todo-md
  systemctl restart todo-md
  sleep 2
  systemctl is-active todo-md && echo 'Service running' || journalctl -u todo-md -n 20
"

# Add nginx location to default server block
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "root@$SERVER" "
  # Add todo-md locations to the existing nginx default config if not already there
  NGINX_CONF=\$(nginx -T 2>/dev/null | grep -m1 '^# configuration file' | awk '{print \$NF}' | tr -d ':')

  if ! grep -r 'todo-md' /etc/nginx/ &>/dev/null; then
    # Inject locations into the first default server block
    python3 - << 'PY'
import re, subprocess, os

# Get main nginx config path
result = subprocess.run(['nginx', '-T'], capture_output=True, text=True)
conf_files = re.findall(r'^# configuration file (.+):$', result.stdout, re.MULTILINE)

# Find the file with the default server block
target = None
for f in conf_files:
    if os.path.exists(f):
        content = open(f).read()
        if 'listen 80' in content:
            target = f
            break

if target:
    content = open(target).read()
    inject = '''
    location = /todo-md { return 301 /todo-md/; }
    location /todo-md/ {
        proxy_pass http://127.0.0.1:8070/;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_read_timeout 60s;
    }
'''
    # Insert before the last closing brace of the first server block
    updated = re.sub(r'(\s*}(\s*server\s*\{|\s*$))', inject + r'\1', content, count=1)
    open(target, 'w').write(updated)
    print(f'Injected into {target}')
else:
    print('Could not find default server config, creating /etc/nginx/conf.d/todo-md.conf')
    open('/etc/nginx/conf.d/todo-md.conf', 'w').write('''server {
    listen 80;
    server_name _;
    location = /todo-md { return 301 /todo-md/; }
    location /todo-md/ {
        proxy_pass http://127.0.0.1:8070/;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }
}
''')
PY
    nginx -t && systemctl reload nginx && echo 'Nginx configured'
  else
    echo 'Nginx already has todo-md config'
  fi
"

echo ""
echo "Done! http://$SERVER/todo-md/"
