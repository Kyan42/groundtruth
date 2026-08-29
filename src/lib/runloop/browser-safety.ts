import { GroundtruthError } from "@/lib/domain/errors";
import type {
  AppProfile,
  ExecutableJourney,
  TestMission,
} from "@/lib/domain/schemas";

export function validateJourneyForReplay(
  journey: ExecutableJourney,
  mission: TestMission,
  profile: AppProfile,
  headUrl: string,
): void {
  const origin = new URL(headUrl);
  if (!["http:", "https:"].includes(origin.protocol) || !isAllowedHost(origin.hostname, profile.safety.allowedHosts)) {
    throw unsafeJourney("The Runloop application URL is outside the trusted host policy.");
  }
  if (journey.missionId !== mission.id) {
    throw unsafeJourney("The discovered journey does not belong to the approved mission.");
  }

  validatePath(mission.startPath, origin, profile.safety.blockedPathPrefixes);
  const firstStep = journey.steps[0];
  if (
    firstStep.action !== "goto" ||
    new URL(firstStep.path, origin).toString() !== new URL(mission.startPath, origin).toString()
  ) {
    throw unsafeJourney("The discovered journey must begin at the approved mission start path.");
  }
  for (const step of journey.steps) {
    if (step.action === "goto") {
      validatePath(step.path, origin, profile.safety.blockedPathPrefixes);
    }
    if (
      step.action === "fill" &&
      typeof mission.fixtureValues?.[step.fixtureValueKey] !== "string"
    ) {
      throw unsafeJourney(`The discovered journey references an unknown fixture value.`);
    }
  }
}

export function isAllowedHost(hostname: string, patterns: string[]): boolean {
  const normalizedHostname = hostname.toLowerCase();
  return patterns.some((pattern) => {
    const normalizedPattern = pattern.toLowerCase();
    if (normalizedPattern.startsWith("*.")) {
      const suffix = normalizedPattern.slice(1);
      return (
        normalizedHostname.endsWith(suffix) &&
        normalizedHostname.length > suffix.length
      );
    }
    return normalizedHostname === normalizedPattern;
  });
}

function validatePath(path: string, origin: URL, blockedPathPrefixes: string[]): void {
  if (!path.startsWith("/") || path.startsWith("//")) {
    throw unsafeJourney("Journey navigation must use an application-relative path.");
  }
  const destination = new URL(path, origin);
  if (destination.origin !== origin.origin) {
    throw unsafeJourney("Journey navigation cannot leave the application origin.");
  }
  if (blockedPathPrefixes.some((prefix) => destination.pathname.startsWith(prefix))) {
    throw unsafeJourney("The discovered journey contains a blocked application path.");
  }
}

function unsafeJourney(message: string): GroundtruthError {
  return new GroundtruthError("unsafe_executable_journey", message, 422, true);
}
