import type { ReviewOffer } from "./ChargingAlert.tsx";

export type ChargingChoice = {
  bay: string;
  digest: string;
  targetSoc?: number;
  terms?: {
    minimumSoc: number;
    waitAndChargeS: number;
    arrivalWorkingS: number;
    offRoute: boolean;
    detour?: string;
    stationState?: string;
  };
};

export function preferredChoice(
  offer: ReviewOffer,
): ChargingChoice | undefined {
  if (!offer.candidate_sha256) return;
  return {
    bay: offer.bay_id,
    digest: offer.candidate_sha256,
    targetSoc: offer.target_soc,
    terms: {
      minimumSoc: offer.minimum_soc,
      waitAndChargeS: offer.wait_working_s + offer.charging_working_s,
      arrivalWorkingS: offer.arrival_working_s,
      offRoute: Boolean(offer.off_route),
      detour: offer.detour_sha256,
      stationState: offer.station_state,
    },
  };
}

export function reviewLimits(choice: ChargingChoice) {
  const terms = choice.terms;
  if (!terms) return null;
  return {
    minimumPercent: Math.floor(terms.minimumSoc * 1000) / 10,
    stopSeconds: Math.ceil(terms.waitAndChargeS / 30) * 30,
  };
}

export function canUseDeferredOffer(
  reviewed: ChargingChoice,
  current?: ChargingChoice,
): boolean {
  const limits = reviewLimits(reviewed);
  const terms = current?.terms;
  if (!current || !terms || !limits) return false;
  return (
    current.bay === reviewed.bay &&
    current.targetSoc === reviewed.targetSoc &&
    terms.stationState === reviewed.terms?.stationState &&
    terms.offRoute === reviewed.terms?.offRoute &&
    (!terms.offRoute ||
      (terms.detour && terms.detour === reviewed.terms?.detour) ||
      current.digest === reviewed.digest) &&
    terms.minimumSoc * 100 >= limits.minimumPercent &&
    terms.waitAndChargeS <= limits.stopSeconds
  );
}
