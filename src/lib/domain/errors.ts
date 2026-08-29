export class GroundtruthError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status: number,
    readonly retryable = false,
    readonly details?: unknown,
  ) {
    super(message);
    this.name = "GroundtruthError";
  }
}

export class SetupRequiredError extends GroundtruthError {
  constructor(code: string, message: string) {
    super(code, message, 503, false);
    this.name = "SetupRequiredError";
  }
}

export function toErrorResponse(error: unknown): {
  status: number;
  body: { error: { code: string; message: string; retryable: boolean; details?: unknown } };
} {
  if (error instanceof GroundtruthError) {
    return {
      status: error.status,
      body: {
        error: {
          code: error.code,
          message: error.message,
          retryable: error.retryable,
          ...(error.details === undefined ? {} : { details: error.details }),
        },
      },
    };
  }

  console.error(error);
  return {
    status: 500,
    body: {
      error: {
        code: "internal_error",
        message: "Groundtruth encountered an unexpected server error.",
        retryable: false,
      },
    },
  };
}
