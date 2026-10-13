// A rough registration year from the Telegram user id, for the «≈» hint beside the
// operator's own date (item 4.8, decision Q15: only the operator's date is stored and
// used). Ids grow with time, so a few public anchor points bracket the year; the
// result is a hint, never a value warming acts on.
const ANCHORS: readonly (readonly [id: number, year: number])[] = [
  [1e8, 2014],
  [2e8, 2016],
  [4e8, 2017],
  [7e8, 2018],
  [1e9, 2019],
  [1.5e9, 2020],
  [2e9, 2021],
  [5.5e9, 2022],
  [6e9, 2023],
  [7e9, 2024],
  [8e9, 2025],
];

export function estimateRegistrationYear(userId: number | null | undefined): number | null {
  if (userId == null || userId <= 0) return null;
  const anchor = ANCHORS.find(([id]) => userId < id);
  return anchor ? anchor[1] : (ANCHORS.at(-1)?.[1] ?? null);
}
