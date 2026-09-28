#!/bin/bash
# E2E + reboot-resilience test for PiCluster. Run from the laptop.
UI=http://192.168.1.223:8000
SSH1=rpi4b-1@192.168.1.223
SSH2=rpi4b-2@192.168.1.222
PW=1922334455
PASS=0; FAIL=0

check() {
  if [ "$2" -eq 0 ]; then
    echo "[PASS] $1"; PASS=$((PASS+1))
  else
    echo "[FAIL] $1"; FAIL=$((FAIL+1))
  fi
}

status() { curl -s --max-time 8 "$UI/api/status"; }

count_alive() {
  status | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
    print(sum(1 for w in d.get('workers',[]) if w.get('alive') and w.get('llama_ok')))
except Exception:
    print(0)"
}

chat_ok() {
  curl -s --max-time 90 -X POST "$UI/api/chat" -H "Content-Type: application/json" \
    -d "{\"prompt\":\"$1\",\"max_tokens\":$2,\"temperature\":0.7,\"seed\":$RANDOM}" \
  | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
    print(d.get('worker','') if d.get('status')=='ok' else '')
except Exception:
    print('')"
}

e2e_core() {
  echo "--- E2E core checks ---"
  local code
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 8 "$UI/")
  [ "$code" = "200" ]; check "E2E: UI serves page (HTTP $code)" $?

  local alive
  alive=$(count_alive)
  [ "$alive" = "2" ]; check "E2E: both workers alive & llama OK (got $alive)" $?

  local tel
  tel=$(status | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
    ws=d.get('workers',[])
    ok = len(ws)==2 and all(w.get('temp_c') is not None and w.get('uptime_s') is not None and w.get('current_model') for w in ws)
    print(1 if ok else 0)
except Exception:
    print(0)")
  [ "$tel" = "1" ]; check "E2E: telemetry on both nodes (temp/uptime/model)" $?

  local w1 w2
  w1=$(chat_ok "Reply with one word: alpha" 24)
  [ -n "$w1" ]; check "E2E: chat served by worker ($w1)" $?
  w2=$(chat_ok "Reply with one word: bravo" 24)
  [ -n "$w2" ]; check "E2E: chat served again ($w2)" $?
}

echo "========== PHASE 1: full E2E =========="
e2e_core

echo ""
echo "========== PHASE 1.5: fault injection =========="
# kill worker2 -> systemd restarts it -> it must come back and re-register
timeout 15 sshpass -p $PW ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=6 $SSH2 "echo $PW | sudo -S systemctl kill picluster-worker@rpi4b-2" 2>/dev/null
ok=0
for i in $(seq 1 12); do
  sleep 5
  a=$(status | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
    print(1 if any(w['name']=='rpi4b-2' and w['alive'] and w['llama_ok'] for w in d.get('workers',[])) else 0)
except Exception:
    print(0)")
  [ "$a" = "1" ] && ok=1 && break
done
[ "$ok" = "1" ]; check "fault: killed worker2 revived by systemd + re-registered" $?

# restart master -> worker1 reconnect loop must re-register
timeout 15 sshpass -p $PW ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=6 $SSH1 "echo $PW | sudo -S systemctl restart picluster-master" 2>/dev/null
ok=0
for i in $(seq 1 12); do
  sleep 5
  a=$(status | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
    print(1 if any(w['name']=='rpi4b-1' and w['alive'] for w in d.get('workers',[])) else 0)
except Exception:
    print(0)")
  [ "$a" = "1" ] && ok=1 && break
done
[ "$ok" = "1" ]; check "fault: worker1 re-registered after master restart" $?

echo ""
echo "========== PHASE 2: reboot resilience x5 =========="
targets=("$SSH2" "$SSH1" "$SSH2" "$SSH1" "$SSH2")
names=(  "rpi4b-2" "rpi4b-1" "rpi4b-2" "rpi4b-1" "rpi4b-2")
for idx in 0 1 2 3 4; do
  t=${targets[$idx]}; n=${names[$idx]}
  echo "--- reboot #$((idx+1)): $n ---"
  timeout 20 sshpass -p $PW ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=8 "$t" "echo $PW | sudo -S systemctl reboot" 2>/dev/null
  echo "  issued $(date +%H:%M:%S); waiting for it to go down..."
  sleep 12
  recovered=""
  t0=$(date +%s)
  for i in $(seq 1 36); do
    sleep 10
    a=$(status | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
    ws=d.get('workers',[])
    ok = any(w['name']=='$n' and w['alive'] and w['llama_ok'] for w in ws)
    others = all(w['alive'] for w in ws if w['name']!='$n')
    print(1 if ok and others else 0)
except Exception:
    print(0)")
    if [ "$a" = "1" ]; then recovered=$(( $(date +%s) - t0 )); break; fi
    echo "  ...waiting ($i/36)"
  done
  if [ -n "$recovered" ]; then
    cw=$(chat_ok "ping round $idx" 16)
    [ -n "$cw" ]; check "reboot #$((idx+1)) $n: recovered ~${recovered}s, chat served by $cw" $?
  else
    check "reboot #$((idx+1)) $n: recovery TIMEOUT (never came back in 6 min)" 1
  fi
done

echo ""
echo "========== PHASE 3: final E2E =========="
e2e_core

echo ""
echo "========== SUMMARY =========="
echo "PASS=$PASS FAIL=$FAIL"
if [ "$FAIL" = "0" ]; then echo "OVERALL: ALL GREEN"; else echo "OVERALL: NEEDS ATTENTION"; fi
