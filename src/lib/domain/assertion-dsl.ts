import { z } from "zod";

export const LocatorSchema = z.discriminatedUnion("by", [
  z.object({ by: z.literal("role"), role: z.string().min(1), name: z.string().min(1).optional() }),
  z.object({ by: z.literal("text"), text: z.string().min(1), exact: z.boolean().optional() }),
  z.object({ by: z.literal("test_id"), value: z.string().min(1) }),
  z.object({ by: z.literal("css"), value: z.string().min(1) }),
]);

export type Locator = z.infer<typeof LocatorSchema>;

const AssertionMetadataSchema = z.object({
  id: z.string().min(1).optional(),
  behavior: z.string().min(1).optional(),
  comparison: z.enum(["pass_only", "exact"]).optional(),
  normalizers: z
    .array(z.enum(["trim", "collapse_whitespace", "application_origin", "network_path"]))
    .optional(),
});

export const AssertionSchema = z.discriminatedUnion("kind", [
  AssertionMetadataSchema.extend({
    kind: z.literal("url"),
    operator: z.enum(["equals", "matches"]).optional(),
    expected: z.string().min(1).optional(),
  }),
  AssertionMetadataSchema.extend({
    kind: z.literal("dom"),
    locator: LocatorSchema,
    state: z.enum(["visible", "hidden"]),
  }),
  AssertionMetadataSchema.extend({
    kind: z.literal("text"),
    locator: LocatorSchema,
    operator: z.enum(["equals", "contains"]).optional(),
    expected: z.string().optional(),
  }),
  AssertionMetadataSchema.extend({
    kind: z.literal("value"),
    locator: LocatorSchema,
    operator: z.enum(["equals", "contains"]),
    expected: z.string(),
  }),
  AssertionMetadataSchema.extend({
    kind: z.literal("network"),
    method: z.string().min(1),
    urlPattern: z.string().min(1),
    expectedStatus: z.number().int().min(100).max(599).optional(),
  }),
  AssertionMetadataSchema.extend({
    kind: z.literal("console"),
    level: z.literal("error"),
    maximumCount: z.number().int().nonnegative(),
  }),
  AssertionMetadataSchema.extend({
    kind: z.literal("page_error"),
    maximumCount: z.number().int().nonnegative(),
  }),
]);

export type Assertion = z.infer<typeof AssertionSchema>;

export const JourneyStepSchema = z.discriminatedUnion("action", [
  z.object({ action: z.literal("goto"), path: z.string().min(1) }),
  z.object({ action: z.literal("click"), locator: LocatorSchema }),
  z.object({
    action: z.literal("fill"),
    locator: LocatorSchema,
    fixtureValueKey: z.string().min(1),
  }),
  z.object({
    action: z.literal("press"),
    locator: LocatorSchema,
    key: z.string().min(1),
  }),
  z.object({
    action: z.literal("wait_for"),
    locator: LocatorSchema,
    state: z.enum(["visible", "hidden"]),
  }),
]);

export type JourneyStep = z.infer<typeof JourneyStepSchema>;
