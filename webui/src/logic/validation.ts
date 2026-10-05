// 数值校验原语：两页共用（DrivePage 的安全整数校验、WeldingPage 的
// JOB/模拟量范围校验）。返回 null 表示"无效，不发命令"。

/** value 是 [minimum, maximum] 内的整数时原样返回，否则 null。 */
export function intIn(value: number, minimum: number, maximum: number): number | null {
  return Number.isInteger(value) && value >= minimum && value <= maximum ? value : null;
}

/** value 是安全整数时原样返回，否则 null（PDO 定位/速度等大数值字段）。 */
export function safeInt(value: number): number | null {
  return Number.isSafeInteger(value) ? value : null;
}
