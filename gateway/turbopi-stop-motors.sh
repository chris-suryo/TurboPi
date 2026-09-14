#!/bin/sh
# Zero all four motors, directly against the robot's RPC server.
#
# Called by systemd as ExecStopPost, which runs whether the gateway exited cleanly, was
# killed, or crashed. The gateway's own shutdown handler covers the clean case; this
# covers the case where the process dies without getting to run any Python at all --
# which is precisely when the motors would otherwise be left spinning.
exec curl -s -m 2 -o /dev/null \
  -X POST http://127.0.0.1:9030/ \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","method":"SetBrushMotor","params":[1,0,2,0,3,0,4,0],"id":99}'
