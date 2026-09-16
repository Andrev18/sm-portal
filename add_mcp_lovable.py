import yaml, json, sys, os
try:
    with open('/opt/data/profiles/edu/config.yaml', 'r') as f:
        conf = yaml.safe_load(f)
    
    if 'lovable' not in conf.get('mcp_servers', {}):
        if 'mcp_servers' not in conf:
            conf['mcp_servers'] = {}
        conf['mcp_servers']['lovable'] = {
            "url": "https://mcp.lovable.dev",
            "enabled": True
        }
        with open('/opt/data/profiles/edu/config.yaml', 'w') as f:
            yaml.dump(conf, f, default_flow_style=False)
        print("ADDED_LOVABLE")
    else:
        print("ALREADY_EXISTS")
except Exception as e:
    print(str(e), file=sys.stderr)
