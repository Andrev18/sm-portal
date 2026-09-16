import yaml, json, sys
try:
    with open('/opt/data/profiles/edu/config.yaml', 'r') as f:
        conf = yaml.safe_load(f)
    print(json.dumps(conf.get('mcp_servers', {}), indent=2))
except Exception as e:
    print(str(e), file=sys.stderr)
