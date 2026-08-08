import next from "eslint-config-next";
import nextCoreWebVitals from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";

/** Here for ONE rule that matters: react-hooks/exhaustive-deps.
 *
 *  tsc already catches the loud failures — wrong props, bad imports, missing fields. What
 *  it cannot see is a useCallback that closes over stale state, which is silent and looks
 *  like the feature simply not working. That is exactly how the language toggle would
 *  have failed if `native` had been left out of start()'s dependency list.
 *
 *  eslint-config-next 16 ships NATIVE flat configs, so this imports them directly.
 *  Routing them through FlatCompat (the shape most guides still show) throws
 *  "Converting circular structure to JSON" before a single file is linted.
 *
 *  no-img-element is off deliberately: the step images are remote Wikipedia thumbnails of
 *  unknown dimensions, which is the case next/image handles worst.
 */
export default [
  ...[next, nextCoreWebVitals, nextTypescript].flatMap((c) =>
    Array.isArray(c) ? c : [c],
  ),
  {
    rules: {
      "@next/next/no-img-element": "off",
    },
  },
  { ignores: [".next/**", "node_modules/**"] },
];
