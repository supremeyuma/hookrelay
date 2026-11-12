// Ingest + fan-out load test. Events are delivered to the local sink by the real worker.
// Reports the number of events the sink received during the run.
//
// Prereqs: compose stack running, sink on the host (port 9099), migrations applied.
//
// Usage:
//   k6 run -e ADMIN_KEY=<key> -e VUS=5 -e DURATION=20s load/delivery.js

import http from "k6/http";
import crypto from "k6/crypto";
import { check } from "k6";
import { Counter } from "k6/metrics";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";
const SINK_BASE = __ENV.SINK_BASE || "http://127.0.0.1:9099";
const DESTINATION_URL =
  __ENV.DESTINATION_URL || "http://host.docker.internal:9099/hook";
const ADMIN_KEY = __ENV.ADMIN_KEY;
const VUS = Number(__ENV.VUS || 5);
const DURATION = __ENV.DURATION || "20s";

const accepted = new Counter("hookrelay_accepted");

export const options = {
  vus: VUS,
  duration: DURATION,
  thresholds: {
    http_req_failed: ["rate<0.001"],
    http_req_duration: ["p(95)<150"],
  },
};

export function setup() {
  if (!ADMIN_KEY) {
    throw new Error("ADMIN_KEY is required");
  }
  http.del(`${SINK_BASE}/requests`);

  const slug = `load-delivery-${Date.now()}`;
  const source = http.post(
    `${BASE_URL}/admin/sources`,
    JSON.stringify({
      slug,
      name: "k6 delivery load",
      rate_limit_per_second: Math.max(VUS * 200, 500),
      burst: Math.max(VUS * 200, 500),
    }),
    {
      headers: {
        "Content-Type": "application/json",
        "X-API-Key": ADMIN_KEY,
      },
    },
  );
  if (source.status !== 201) {
    throw new Error(`source setup failed: ${source.status} ${source.body}`);
  }

  const destination = http.post(
    `${BASE_URL}/admin/sources/${slug}/destinations`,
    JSON.stringify({ url: DESTINATION_URL }),
    {
      headers: {
        "Content-Type": "application/json",
        "X-API-Key": ADMIN_KEY,
      },
    },
  );
  if (destination.status !== 201) {
    throw new Error(
      `destination setup failed: ${destination.status} ${destination.body}`,
    );
  }

  return { slug, secret: source.json("secret") };
}

export default function (data) {
  const body = JSON.stringify({ type: "load.delivery", amount: 100 });
  const timestamp = Math.floor(Date.now() / 1000);
  const signature = crypto.hmac(
    "sha256",
    data.secret,
    `${timestamp}.${body}`,
    "hex",
  );

  const response = http.post(`${BASE_URL}/ingest/${data.slug}`, body, {
    headers: {
      "Content-Type": "application/json",
      "X-HookRelay-Signature": `t=${timestamp},v1=${signature}`,
      "X-Idempotency-Key": `delivery-${__VU}-${__ITER}-${Date.now()}`,
    },
  });

  if (response.status === 202) {
    accepted.add(1);
  }
  check(response, {
    "ingest accepted": (r) => r.status === 202,
  });
}

export function teardown() {
  // Give the poller/worker a moment to drain the queue.
  const response = http.get(`${SINK_BASE}/requests`);
  if (response.status === 200) {
    console.log(
      `sink received ${response.json().length} deliveries during the run`,
    );
  }
}
