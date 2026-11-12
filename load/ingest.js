// Ingest load test. Ten VUs keep up with ~700 rps on the reference host; a fixed VU count
// avoids the pathological VU explosion that an unbounded arrival-rate executor causes when
// the first responses are slow.
//
// Usage:
//   k6 run -e ADMIN_KEY=<key> -e VUS=10 -e DURATION=30s load/ingest.js

import http from "k6/http";
import crypto from "k6/crypto";
import { check } from "k6";
import { Counter } from "k6/metrics";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";
const ADMIN_KEY = __ENV.ADMIN_KEY;
const VUS = Number(__ENV.VUS || 10);
const DURATION = __ENV.DURATION || "30s";
const DUPLICATE_SHARE = Number(__ENV.DUPLICATE_SHARE || 0.1);

const accepted = new Counter("hookrelay_accepted");
const duplicates = new Counter("hookrelay_duplicates");

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
  const slug = `load-${Date.now()}`;
  const response = http.post(
    `${BASE_URL}/admin/sources`,
    JSON.stringify({
      slug,
      name: "k6 ingest load",
      rate_limit_per_second: Math.max(VUS * 200, 2000),
      burst: Math.max(VUS * 200, 2000),
    }),
    {
      headers: {
        "Content-Type": "application/json",
        "X-API-Key": ADMIN_KEY,
      },
    },
  );
  if (response.status !== 201) {
    throw new Error(`source setup failed: ${response.status} ${response.body}`);
  }
  return { slug, secret: response.json("secret") };
}

export default function (data) {
  const body = JSON.stringify({ type: "load.test", amount: 100 });
  const timestamp = Math.floor(Date.now() / 1000);
  const signature = crypto.hmac(
    "sha256",
    data.secret,
    `${timestamp}.${body}`,
    "hex",
  );
  const idempotencyKey =
    Math.random() < DUPLICATE_SHARE
      ? `dup-${__VU}`
      : `evt-${__VU}-${__ITER}-${Date.now()}`;

  const response = http.post(`${BASE_URL}/ingest/${data.slug}`, body, {
    headers: {
      "Content-Type": "application/json",
      "X-HookRelay-Signature": `t=${timestamp},v1=${signature}`,
      "X-Idempotency-Key": idempotencyKey,
    },
  });

  if (response.status === 202) {
    accepted.add(1);
  } else if (response.status === 200) {
    duplicates.add(1);
  }

  check(response, {
    "ingest accepted or duplicate": (r) => r.status === 202 || r.status === 200,
  });
}
