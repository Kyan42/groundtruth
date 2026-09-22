
Tags: [[entrepreneurship]]


### High Level:

Groundtruth fires every time a PR is opened. It does two things:

1. Builds testable claims and browser journeys for this new feature, based on the user's *intent*
2. Runs browser journyes for possible regression paths

Each of these claims maps to a browser journey and playwright script that can actually run the test in the browser, end-to-end, with assertions that state whether or not it was successful. 

multiple claims -> one browser journey -> one playwright script. 

if a playwright script doesn't already exist for a given claim, the model makes one.

This makes most sense as a Github App. A webhook should hit our endpoint whenever a PR Is opened with all the information we want. 

### A browser journey
A browser journey is a walk through the app. It's the collection of a starting point, a series of actions you take, and things you check for after every action. Example: Starting from the chat page, take the action of typing into the chatbox. The text should be visible in the chat box. Take the action of hitting enter. A new dialog should be sent. A response should start being generated. 

This example would be a single browser journey that's testing three claims: typing in the chatbox should register text, sending text from this box should show a new dialgoue, and sending text from this box should make an API call for a response. Maybe we also want to test that the response is actually returned and displayed. this would be another claim, that's part of the overarching browser journey. 
### Path to create a new test script (or "browser journey")
1. Start a dev server on the PR head
2. Start an agent loop that can control the browser through playwright. 
3. Agent observes each state given a screenshot and a structured DOM
4. the agent walks through the browser to test the claims built for this PR. a journey should answer multiple claims

Lots of questions here:
1. How does the agent know what path in the UI tests this claim? should it build a knowledge graph of the UI/UX? 
2. How does the agent decide how to build the playwright script? Specifically:
	1. how does it pick IDs of components (make sure they are stable)
	2. how does it pick assertions to make (critical for testing the claim)

#### Q: we ideally want to cache these. the question becomes, how? if the claims are AI generated on every PR, how do we actually make them fall within a discrete set? 

As the codebase changes, these cached tests may become stale. They may no longer reference the most up to date components, their IDs, or the path itself may have changed. The agent should also be able to heal, or recover, these tests.

#### Q: how does the healing work? how does the agent:
1. determine that a test is outdated/broken as opposed to genuinely failing
2. fix that test without rerunning the path to create a new test script (or is that the only way?)


### The Flow
When a PR is opened, at a high level, there's three steps of processing.

#### Step 1: Claim Building
The evidence bundle gets passed into an agent that builds a list of claims. Conceptually, if the PR is adding the feature to "archive a project" the claims might be:
1. Projects should have an archive button next to them
2. Clicking archive should make a popup appear asking the user to confirm
3. Once archived, the project should no longer appear in the "active" page
4. Once archived, the project should appear in the "archive" page 



The result of this step should be a list of claims, where each is a testable statement. 

##### Step 1.1: User Verification
After the claims are made, they should be presented to the user, ideally through a github comment. The user can either approve the claims, or edit them. I haven't decided yet how editing will work. They can either directly type in a new claim, or provide feedback and the model will re-generate its claims. 

After claims are made, they should be compared against existing claims. If some behavior was meant to be changed, the old claim shouldn't be run as as a regression test -- this will falsely mark a regression.

v0: devs can edit comment text directly. a slash command or a reaction approves and starts the rest of the process.

%%##### Step 1.2: Starting State
Before we can actually open up a browser and start working it, we need to decide the starting state. Does the browser need a specific auth? Does it need to start at a certain page? Does it need seeded data of some kind? This should be figured out before the browser agent runs because it relies on these things to be able to properly test the claim.%%it's 
#### Step 2: Browser Exploration
These claims should then be passed into a browser agent that can use playwright to drive the browser. It's goal: find a path through the browser to test the claims.

It should be given setup functions (think auth). It should be given browser controls. * And it should be given a list of existing page objects that we've built, with the controls for their UI component. (there may be a better option than this. The idea is I want the agent to have some knowledge on how the webpage works without needing to figure everything out from scratch. maybe this is not a V0 feature). 

The agent should click through the browser and either verify all claims. Output should be per-claim status and a trace of what it did.

each claim should get a status at the end:
1. verified: self-explanatory
2. failed: an explicit contradiction to this claim was made
3. blocked: a previous claim failed, which this one relied on. never reached
4. unreachable: the agent couldn't find a path to actually test this claim. could be a UX issue or agent error
5. error: an infra error prevented us from seeing this test.

#### Step 3: Compile
Taking the output of step two--the traces and claim statuses-- build the journey files and playwright scripts associated with them. This playwright script should get run in a fresh sandbox. 
##### V1: this should also get run in main to ensure that all claims FAIL in main. if a claim passes in main, it probably isn't testing anything meaningful. Need to figure out how to recover from this situation. 

Given that the playwright script runs in a fresh sandbox and succeeds, this gets saved in our registry of browser journey and scripts, and we return the results. 

NOTE: on second thought. these scripts probably shouldn't get saved until the PR gets merged. This presents a situation where we have two conflicting PRs, and we try to save both of their tests. instead, once that situation is reconciled by the dev and a PR is merged, we assume that it's safe to save these browser journeys and scripts. 
#### Step 4: Regression Tests
Input to this step is the full code diff, looking at what files and surfaces were touched. Use this to search for existing browser journeys we have, in our registry, which test claims on this path. Exclude claims that should have changed behavior (see step 1). 

v0: this step does NOT make new tests. it only looks at existing tests that were created from previous runs of this app, on previous PRs. 
v1: an optional onboarding step generates browser journeys for core functionality.
v1: OR an optional step, per PR, allows the regression handler to create only the tests it needs, at that time, to test regressions. this makes more sense. Then these tests get added to the registry.

A few outcomes from the regression testing:
1. It succeeds. yay! no other work
2. It fails: run it again on main
	1. It fails on main as well: report this issue, but don't blame the PR. for v0 this is a decent stopping point. in the future, there should be more work done here to figure out why it's failing + possibly heal
	2. it succeeds on main: possible regression. verify that this behavior change was NOT intended. v0: ask the user, was this change intended? yes -> report success. no -> report fail. or smth like that. v1: maybe automatically reconcile? but also human in the loop may be helpful.


### Config File & Onboarding
We need to collect and save some repo-wide information about how to start a dev server, how to set up auth, etc. When the app is first installed, the user can choose to launch the onboarding flow and have it scan the codebase and figure out how these details itself. 

The information on starting a dev server should be performed in every sandbox, when the run is starting. need to decide if this will be deterministic or if it will be handled by a small agent. 

Test accounts will need to be saved somewhere by the user ... maybe an environment file for this? not sure yet.

v0: either a really simply onboarding flow that just finds how to make the dev server, or assume the user makes this themselves.


### The Dashboard
Right now, the flow is that a github comment writes the claims, the user confirms, then the browser journeys are built/retrieved and run. Once the tests are run, we should create a dashboard. The dashboard shows a sidebar with each browser journey. clicking on the browser journey shows a video demonstrating what the agent did, including a cursor showing what the agent clicked. Beneath the video you can find the steps, what is checked at each step, and also console/network events that were captured. 

The UI for this is already fully built out. 


### Detection and Intent
When the PR is opened, we want the best evidence bundle to build intent-based claims. The PR body and issues are ok proxies of this, but the problem is they are written by agents. The best source would be the transcript of the session (or sessions) that actually built the feature and published the PR. 

A workaround: Add to the repo instruction files for the agent asking it to include a section on "user intent" whenever it writes a PR. It's not perfect, still just a proxy, but maybe a bit closer.

%%### How do we make our claims testable
What is a test: a procedure that produces a definite outcome. Defined very precisely, it should say: "Given these precondition (e.g. starting on this page, in this account, with this action previously done) and then performing this sequence of actions (clicking a button, typing in text, etc. ), we should see this measurable result."

The starting state has to be constructable. Actions have to be expressed as user actions. The outcome has to be observable through the browser. 
The outcome should also have a clear way to fail.
%%
%%##### EXAMPLE

A PR titled "Add ability to archive projects" might have this as a claim:

"_Given_ a logged-in user with at least one active project on `/projects`, _when_ they open a project card's menu and choose Archive, _then_ that project no longer appears in the active list and a toast confirms it."


### The shape of a claim

Claims need to be machine identifiable because we want them to be "cached"; that is, once a test is built, it should be key-able by some unique property of the claim, such that if a future agent needs to test that same claim, it can look it up.

Note that this lookup should only ever happen for regression tests. The claims made to test the new feature should not be able to be looked up, because they should be testing new paths. However, parts of the browser journey may overlap. 

A claim should contain two parts:
1. Envelope: the part that is key-able. this should be multiple entries that are fixed
2. Payload: the part that's more semantic. This will be turned into the playwright script.

#### Claim: Envelope

This consists of a few components:
1. id: a unique name for the claim. built from surface, persona, and action. two of the same claim should have the same id
2. surface: the URL path where the claim begins. e.g. /projects or /chat/:id
3. persona: the "account type" this test is ran on. can be anonymous, user, admin, etc. this should correlate to a way to actually "build" this persona in the config file. 
4. fixtures: the data that must exist before the test runs. this maps to a fixed set of pre-defined configs on what these fixtures can be. if the agent needs a new one, it can create it, or have the repo owner create it (this needs to be fleshed out more)
	1. possibly, there should be a fixture for each data type that we might want to prefill. the point of fixtures is to allow data to be added without having to do it through the browser. 
	2. the challenge is: does the agent write the fixture code? if so, how do we know it is good? what do we do if this goes stale? 
	3. the agent should probably write this by itself, first, but then update if it believes 
%%


#### Q: Is there a way we can reuse parts of the browser journey? Maybe abstract pages into classes with functions for common behavior.
