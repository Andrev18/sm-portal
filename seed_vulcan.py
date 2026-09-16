import os, urllib.request, json, sqlite3

def run():
    with open('/opt/data/profiles/edu/.env') as f:
        env = f.read()
    ha_token = [line.split('HA_MCP_TOKEN=')[1].strip() for line in env.split('\n') if line.startswith('HA_MCP_TOKEN=')][0]
    
    def get_ha(entity):
        try:
            req = urllib.request.Request(f'http://10.10.10.123:8123/api/states/{entity}', headers={'Authorization': 'Bearer '+ha_token, 'Content-Type': 'application/json'})
            return json.loads(urllib.request.urlopen(req).read().decode('utf-8'))
        except: return {}
        
    plan = get_ha('sensor.vultron_plan_stanislaw_mikos_next')
    plan_data = plan.get('attributes', {}).get('lekcje', [])
    if not plan_data:
        plan_data = get_ha('sensor.vultron_plan_stanislaw_mikos_curr').get('attributes', {}).get('lekcje', [])
        
    oceny_data = get_ha('sensor.vultron_oceny_stanislaw_mikos_p1').get('attributes', {}).get('oceny', [])
    
    conn = sqlite3.connect('/opt/data/.hermes/plans/sm-portal/db/portal.db')
    c = conn.cursor()
    try: c.execute('ALTER TABLE subjects ADD COLUMN teacher TEXT')
    except: pass
    try: c.execute('ALTER TABLE subjects ADD COLUMN avg TEXT')
    except: pass
    try: c.execute('ALTER TABLE subjects ADD COLUMN prop TEXT')
    except: pass
    for l in plan_data:
        if 'p' in l and 'n' in l: c.execute('UPDATE subjects SET teacher=? WHERE name=?', (l['n'].strip(), l['p'].strip()))
    for o in oceny_data:
        if o.get('przedmiot'):
            c.execute('UPDATE subjects SET avg=?, prop=? WHERE name=?', (f"{float(o.get('srednia')):.2f}" if o.get('srednia') else "-", o.get('proponowana','-'), o['przedmiot'].strip()))
    conn.commit()
    print('DB UPDATED WITH VULCAN METADATA')

run()