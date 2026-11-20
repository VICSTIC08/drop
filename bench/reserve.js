// k6 script: every "virtual user" is a buyer who tries to reserve one unit.
// k6 runs outside the app and talks to it over real HTTP, like real buyers would.
import http from 'k6/http';
import exec from 'k6/execution';
import { Counter } from 'k6/metrics';

const held = new Counter('held');
const soldOut = new Counter('sold_out');
const unexpected = new Counter('unexpected');

// 409 "sold out" is a correct answer, not a failure.
http.setResponseCallback(http.expectedStatuses(201, 409));

export const options = {
  scenarios: {
    buyers: {
      executor: 'shared-iterations',
      vus: Number(__ENV.VUS),
      iterations: Number(__ENV.BUYERS),
      maxDuration: '5m',
    },
  },
  summaryTrendStats: ['min', 'med', 'p(95)', 'p(99)', 'max', 'avg'],
};

// A different buyer for every request, built from the request's sequence number.
function buyerId(n) {
  return '00000000-0000-4000-8000-' + n.toString(16).padStart(12, '0');
}

export default function () {
  const url = `${__ENV.BASE_URL}/drops/${__ENV.DROP_ID}/reservations`;
  const params = { headers: { 'X-User-Id': buyerId(exec.scenario.iterationInTest) } };
  const response = http.post(url, null, params);
  if (response.status === 201) {
    held.add(1);
  } else if (response.status === 409) {
    soldOut.add(1);
  } else {
    unexpected.add(1);
  }
}

export function handleSummary(data) {
  return { [__ENV.SUMMARY]: JSON.stringify(data) };
}
