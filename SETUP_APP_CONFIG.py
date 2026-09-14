# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "5"
# ///
# DBTITLE 1,Redeploy with env vars in app.yaml
import requests, json, time
from databricks.sdk import WorkspaceClient

w = WorkspaceClient()
host = w.config.host.rstrip("/")
headers = {**w.config.authenticate(), "Content-Type": "application/json"}

APP_NAME = "databricks-live-voice"
SOURCE = "/Workspace/Users/kevin.ippen@databricks.com/talk-to-your-data/"

# Env vars now set in app.yaml (value: fields). Just redeploy.
print("Deploying with env vars from app.yaml...")
r = requests.post(f"{host}/api/2.0/apps/{APP_NAME}/deployments", headers=headers,
                  json={"source_code_path": SOURCE}, timeout=60)
dep = r.json()
print(f"  deployment_id={dep.get('deployment_id')}, state={dep.get('status',{}).get('state')}")

for i in range(30):
    time.sleep(5)
    a = requests.get(f"{host}/api/2.0/apps/{APP_NAME}",
                     headers={**w.config.authenticate()}, timeout=10).json()
    app_state = a.get('app_status',{}).get('state','?')
    compute  = a.get('compute_status',{}).get('state','?')
    dep_state = a.get('active_deployment',{}).get('status',{}).get('state','?')
    msg = a.get('active_deployment',{}).get('status',{}).get('message','')
    print(f"  [{(i+1)*5:>3}s] app={app_state}  compute={compute}  deploy={dep_state}")
    if dep_state == 'FAILED':
        print(f"\n  Deploy failed: {msg}")
        break
    if app_state == 'RUNNING' and dep_state == 'SUCCEEDED':
        print(f"\n  LIVE at {a['url']}")
        print(f"  Frontend: {a['url']}/")
        print(f"  Health:   {a['url']}/health")
        print(f"  Docs:     {a['url']}/api/docs")
        break
else:
    print("\n  Didn't reach RUNNING in 2.5min — check logs.")