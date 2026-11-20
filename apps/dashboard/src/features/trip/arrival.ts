export function remainingArrival(
  arrivalWorkingS: number | undefined,
  currentWorkingS: number,
): string | null {
  if (
    arrivalWorkingS === undefined ||
    !Number.isFinite(arrivalWorkingS) ||
    !Number.isFinite(currentWorkingS) ||
    currentWorkingS < 0 ||
    arrivalWorkingS < currentWorkingS
  )
    return null;
  const seconds = Math.ceil(arrivalWorkingS - currentWorkingS);
  if (seconds === 0) return "Due now";
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;
  const time = [
    hours && `${hours} h`,
    minutes && `${minutes} min`,
    remainder && `${remainder} s`,
  ]
    .filter(Boolean)
    .join(" ");
  return `${time} working time`;
}
