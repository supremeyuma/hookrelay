import http from "k6/http";

export const options = {
  scenarios: {
    health: {
      executor: "constant-arrival-rate",
      rate: 500,
      timeUnit: "1s",
      duration: "10s",
      preAllocatedVUs: 50,
      maxVUs: 400,
    },
  },
  thresholds: {
    http_req_duration: ["p(95)<100"],
  },
};

export default function () {
  http.get(`${__ENV.BASE_URL || "http://api:8000"}/healthz`);
}
