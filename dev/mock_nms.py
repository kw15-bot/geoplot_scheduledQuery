"""合成データのモックNMS-API（notam_cn_probe.py を手元で試す用。仕様 nms-api.yaml 1.0.18 のスキーマに準拠、実データではない）。
使い方: python dev/mock_nms.py &  →  NMS_HOST=http://127.0.0.1:8765 NMS_CLIENT_ID=ID NMS_CLIENT_SECRET=SECRET python notam_cn_probe.py"""
import base64, json, datetime as dt
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

def feat(num, icao, fir, cls, acc, q, coords, radius, geom=None):
    return {"type":"Feature","properties":{"coreNOTAMData":{"notam":{
        "id":"NMS_ID_"+num.replace("/","").replace(" ",""),"series":"A","number":num,"type":"N",
        "affectedFir":fir,"selectionCode":q,"location":icao[-3:],"icaoLocation":icao,"classification":cls,
        "accountId":acc,"effectiveStart":"2026-09-21T00:00:00.000Z","effectiveEnd":"2026-09-23T00:00:00.000Z",
        "coordinates":coords,"radius":radius,"lowerLimit":"SFC","upperLimit":"FL200","text":"SYNTHETIC",
        "lastUpdated":"2026-09-21T01:00:00.000Z"}}},
        "geometry": geom or {"type":"GeometryCollection","geometries":[{"type":"Point","coordinates":[121.0,31.0]}]}}

FEATS = [
 feat("A0101/26","ZBPE","ZBPE","INTERNATIONAL","ZBBBYNYX","QRDCA","3900N11600E","30"),   # 中国FIR
 feat("A0102/26","ZSPD","ZSHA","INTERNATIONAL","ZSSSYNYX","QWMLW","3115N12143E","10"),   # 中国空港
 feat("A0103/26","ZGZU","ZGZU","INTERNATIONAL","ZGGGYNYX","QRTCA","2300N11300E","50"),   # 中国FIR
 feat("A0104/26","KZBW","ZBW","INTERNATIONAL","KZBW","QXXXX","4200N07100W","5"),         # 米ARTCC(衝突テスト)
 feat("A0105/26","KZHU","ZHU","DOM","HOU","QXXXX","2900N09500W","5"),                    # 米ARTCC
 feat("A0106/26","ZMUB","ZMUB","INTERNATIONAL","ZMUBYNYX","QRTCA","4755N10653E","20"),   # モンゴル(除外)
 feat("A0108/26","VHHH","VHHK","INTERNATIONAL","VHHHYNYX","QRTCA","2215N11410E","20"),   # 香港
 feat("A0109/26","RCTP","RCAA","INTERNATIONAL","RCTPYNYX","QRDCA","2500N12130E","30"),   # 台湾
 feat("A4703/26","ZGZU ZSHA","ZXXX","INTERNATIONAL","ZBBBYNYX","QRTCA","2536N11651E","39"),  # 複数地点+ZXXX
 feat("A0107/26","RJTG","RJJJ","INTERNATIONAL","RJTGYNYX","QRTCA","3500N13900E","10"),   # 日本(除外)
]

class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def send(self, code, obj):
        b = json.dumps(obj).encode(); self.send_response(code)
        self.send_header("Content-Type","application/json"); self.send_header("Content-Length",str(len(b)))
        self.end_headers(); self.wfile.write(b)
    def do_POST(self):
        if self.path=="/v1/auth/token":
            exp = "Basic "+base64.b64encode(b"ID:SECRET").decode()
            if self.headers.get("Authorization")!=exp: return self.send(401,{"message":"Unauthorized"})
            n=int(self.headers.get("Content-Length",0)); body=self.rfile.read(n).decode()
            if "grant_type=client_credentials" not in body: return self.send(400,{"message":"Required param : grant_type"})
            return self.send(200,{"access_token":"TOK","expires_in":"1799","status":"approved"})
        self.send(404,{})
    def do_GET(self):
        u=urlparse(self.path); q={k:v[0] for k,v in parse_qs(u.query).items()}
        if self.headers.get("Authorization")!="Bearer TOK": return self.send(401,{"status":"401","message":"Unauthorized"})
        if u.path=="/nmsapi/v1/ping": return self.send(200,{"status":"Success"})
        if u.path=="/nmsapi/v1/notams":
            if self.headers.get("nmsResponseFormat") not in ("AIXM","GEOJSON"): return self.send(400,{"status":"400","message":"Bad Request"})
            if not q: return self.send(400,{"status":"400","message":"Bad Request"})
            fs = FEATS
            if "lastUpdatedDate" in q:
                since = dt.datetime.strptime(q["lastUpdatedDate"],"%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
                if dt.datetime.now(dt.timezone.utc)-since > dt.timedelta(hours=2):   # 広い窓は408を再現
                    return self.send(408,{"status":"408","message":"Request Timeout"})
            if "location" in q: fs=[f for f in FEATS if f["properties"]["coreNOTAMData"]["notam"]["icaoLocation"]==q["location"]]
            return self.send(200,{"status":"Success","data":{"geojson":fs}})
        if u.path=="/nmsapi/v1/locationseries":
            rows=[{"locationId":"PVG","icaoId":"ZSPD","internationalSeries":{"A":{"accountId":"ZSSS","aftnAddress":"ZSSSYNYX"}}},
                  {"locationId":"BJS","icaoId":"ZBPE","internationalSeries":{"A":{"accountId":"ZBBB","aftnAddress":"ZBBBYNYX"}}},
                  {"locationId":"JFK","icaoId":"KJFK","internationalSeries":{"A":{"accountId":"FSIA","aftnAddress":"FSIAYNYX"}}}]
            return self.send(200,{"status":"Success","data":{"locationSeries":rows}})
        self.send(404,{})

HTTPServer(("127.0.0.1",8765),H).serve_forever()
