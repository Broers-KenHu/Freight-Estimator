type ChargeAmount = string | number | null | undefined

type ChargeLineLike = {
  amount_ex_gst?: ChargeAmount
  gst_amount?: ChargeAmount
  amount_inc_gst?: ChargeAmount
}

export const hasNonZeroAmount = (value: ChargeAmount) => {
  if (value === null || value === undefined || value === '') return false
  const amount = Number(value)
  return Number.isFinite(amount) && Math.abs(amount) > 0.000001
}

export const hasNonZeroChargeLine = (line: ChargeLineLike) =>
  hasNonZeroAmount(line.amount_ex_gst) || hasNonZeroAmount(line.gst_amount) || hasNonZeroAmount(line.amount_inc_gst)

export const nonZeroChargeLines = <T extends ChargeLineLike>(lines?: T[] | null) => (lines || []).filter(hasNonZeroChargeLine)
