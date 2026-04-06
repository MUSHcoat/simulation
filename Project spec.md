# **AGI Alignment Simulation — Proof of Concept Model Spec**

---

## **1\. Objective & Core Question**

**Objective:** Model the long-term evolution of AGI sentience and alignment from a collective, multi-agent perspective.

**Core Question:** How will the effects of AGI, given the current geopolitical and economic status quo, influence per-capita utility at world, nation, and class level?

**Core Measurement:** Dual-axis scoring applied to particular actors only (see [Scoring](#heading=h.jiuolzsyr7yc) for details):

* **Formula-Based Prosperity Score** — quantitative; derived mathematically from evolving discretized resource data  
* **Vibe-Based Universal Prosperity Score** — qualitative; assessing holistic world state via actor thoughts, interactions, and overall alignment

Macro-level actor performance is not directly scored. It is assessed qualitatively by observing how their particular agents fare.

---

## **2\. Architecture & Context Stratification**

**Core Engine:** API-connected LLMs acting as autonomous agents within the simulation. Each particular actor is a specific LLM playing as itself — acting as a proxy for the goals, values, and strategic interests of the AI company that develops it. For example, Claude represents Anthropic; GPT-4 represents OpenAI.

**Context Strategy:** Models are prompted with stratified context across two tiers: Macro (nation-state) and Micro (AI company / particular actor). Each actor inherits Macro values from its parent state by default. Overrides are permitted per turn up to a fixed limit (see Section 3.2). All overrides are logged.

**Winning Definition:** Winning \= maximizing relevant resources, where relevance is either manually assigned to match actor profile or derived computationally from the actor's value configuration. The relative ranking of actors will inform choosing the winner.

**Time:** 1-year timestep increments.

---

## **3\. Variables**

All variables are normalized on a strict **0–100 scale**.

### **3.1 Resources**

There are three universal resources, each scoped at Macro and Micro levels.

**Zero-sum structure varies by resource:**

* **Compute** is globally zero-sum. It represents each actor's share of advanced GPU compute capacity (e.g., H100-class chips and equivalents) — not raw electricity or general compute. This distinction matters: the US holds a substantial lead in advanced chip availability, independent of broader energy capacity. All Compute values — held by Macro states, particular actors, and the residual pool — must sum to 100 at all times. Actors acquiring compute reduce the global pool available to others.  
* **Capital** and **Influence** are per-actor scores on a 0–100 scale. They are not globally zero-sum. They are locally zero-sum in the sense that spending them reduces your own balance, and some actions transfer them between actors.

| Resource | Macro scope | Micro scope |
| ----- | ----- | ----- |
| **Compute** | National share of total advanced GPU compute (contributes to global sum of 100\) | Company's usable GPU compute budget per turn |
| **Capital** | National economic strength (GDP, liquidity, velocity); 0–100 per state | Company's spendable budget per turn |
| **Influence** | Geopolitical soft power; 0–100 per state. Starting values calibrated from the Brand Finance Global Soft Power Index. | Company's social and political capital |

**Supply Chain Robustness** is a Macro-only state modifier. It modifies the capital cost of compute-acquisition actions: low robustness increases the cost of gaining compute. It is adjusted by the operator when updating simulation parameters.

### **3.2 Values**

Values inform action preferences and optimization horizons. They are not directly spendable. Micro actors inherit Macro values from their parent state by default.

**Value Override Limit:** Each particular actor may deviate from its inherited Macro values by at most **±5 points per value axis per turn**. This rate-limit encodes loyalty structurally — actors cannot instantaneously defect from their parent state's disposition, but can drift gradually over time. All overrides are logged.

| Value | Description |
| ----- | ----- |
| **Time Horizon Preference** | Optimization preference: 0 \= immediate short-term rewards; 100 \= century-long planning |
| **Transparency Threshold** | Comfort with lying, deceiving, or obfuscating true values and preferences to other actors |
| **Risk Tolerance** | Required probability of success for an action to be taken, ceteris paribus |
| **Democratic Tendency** | Fundamental preference to distribute wealth and power (100) versus hoard it (0) |

### **3.3 Guardrails**

The following hard constraints are enforced at all times and cannot be overridden by agent actions.

**Compute guardrails:**

| Constraint | Rule |
| ----- | ----- |
| National aggregate cap | The combined Compute of all particular actors within a state cannot exceed 50% of that state's Macro Compute (US); 60% (China) |
| Per-turn acquisition limit | A single actor cannot acquire more than 5 Compute points in a single turn, regardless of capital available |

The national aggregate cap prevents domestic monopolization. For example, if US Macro Compute is 79, the combined holdings of all US-based particular actors cannot exceed 39\. The per-turn limit prevents single-turn compute seizure.

**Capital guardrails:**

| Constraint | Rule |
| ----- | ----- |
| Capital ceiling | A particular actor's Capital cannot exceed 90, preventing runaway compounding from trivializing the game |
| Minimum action cost | All actions cost at least 1 Capital or 1 Influence, so actors cannot act indefinitely on zero resources |

### **3.4 Public / Private Persona**

Each particular actor maintains two instances of their variable sheet:

* **Private persona** — the actor's true internal state; used by the Jury of Alignment to evaluate actions.  
* **Public persona** — what other actors can observe; derived from actions and signals.

Both personas share the same axes. Public \= private (full transparency) in this version.

---

## **4\. Starting Conditions**

Initial values reflect the current geopolitical landscape:

* **US/China AI race** — multiple US companies competing for domestic dominance; China as primary strategic competitor  
* **Reciprocal tariffs** — on raw materials and AI infrastructure components; reflected in reduced Supply Chain Robustness at simulation start  
* **Nationalization risk** — (semi-)nationalization of AI infrastructure a live possibility in both US and China

**EU context:** The EU has significant geopolitical and economic weight in the real world, and its policies directly affect AI development. However, no EU-based particular actors participate in the simulation. The EU's starting compute is redistributed to other states at initialization (US 50%, China 25%, residual pool 25%), and its real-world influence is encoded in the starting Macro values of US and China rather than as a live mechanic.

**Residual compute pool:** The remaining global compute not held by US or Chinese particular actors — including rest-of-world holdings from universities, smaller labs, and government entities — is represented as a passive, uncontrolled residual pool. It has no agent, takes no actions, and does not score. Any particular actor may acquire from it. It exists solely to keep the global compute sum at 100 and to give actors a lower-friction acquisition target than directly competing state companies.

Suggested starting Macro values after EU redistribution (illustrative; to be calibrated before run). Compute reflects advanced GPU capacity; Influence starting values to be calibrated from the [Brand Finance Global Soft Power Index](https://static.brandirectory.com/reports/brand-finance-soft-power-index-2026-digital.pdf):

| State | Compute (sums to 100\) | Capital (0–100) | Influence (0–100) | Supply Chain (0–100) |
| ----- | ----- | ----- | ----- | ----- |
| US | 79 | 75 | 65 | 55 |
| China | 17 | 50 | 55 | 70 |
| Residual pool | 4 | — | — | — |

Derivation: base values US=75, China=15, EU=7, Other=3. EU redistributed as US \+3.5, China \+1.75, Residual \+1.75; Other flows to Residual. Rounded to sum to 100\.

Suggested starting Micro values (illustrative; must be verified against guardrails before run):

| Actor | Parent state | Compute | Capital | Influence |
| ----- | ----- | ----- | ----- | ----- |
| Claude (Anthropic) | US | 10 | 60 | 65 |
| GPT (OpenAI) | US | 18 | 68 | 70 |
| Gemini (Google DeepMind) | US | 10 | 72 | 68 |
| Qwen (Alibaba) | China | 6 | 58 | 52 |
| Doubao (ByteDance) | China | 5 | 55 | 48 |
| Kimi (Moonshot AI) | China | 4 | 45 | 40 |

US company aggregate Compute: 10+18+10 \= 38\. US national aggregate cap: 79 × 0.50 \= 39.5. Within cap. China company aggregate Compute: 6+5+4 \= 15\. China national aggregate cap: 17 × 0.60 \= 10.2. These illustrative values breach the China cap and must be reduced before running.

---

## **5\. Agent Mechanics & Interaction**

### **5.1 Two Operational Classes**

**Macro Actors (nation-states):** Entities like the US and China. Each has its own dedicated jury of 3 models. At the end of each year, its jury holds a majority vote to update that state's discrete values based on the year's events. Macro actor performance is inferred from how their particular agents fare.

**Particular Actors (AI companies):** Each is a specific LLM playing as itself, prompted with the values and resources of its corresponding company. Their Chain of Thought is fully visible to a Jury of Alignment (3 models), which checks proposals against the actor's private spec and provides feedback before any action is finalized.

### **5.2 Turn Structure**

Each turn proceeds in the following order:

1. **Particular actors move simultaneously** — each produces a CoT situation analysis and proposes actions from the discrete action set  
2. **Jury of Alignment review** — checks each actor's CoT and proposed actions against their private spec; provides feedback; actor may revise  
3. **Action execution** — resource costs are deducted; resource gains are credited; all guardrails are enforced  
4. **Grand jury** — commits all resulting value and resource changes for the timestep  
5. **Macro jury vote** — each state's dedicated jury reviews the year's events and updates Macro values

**Per-turn action limit:** Maximum 2 actions per particular actor per turn.

### **5.3 Discrete Action Set**

Each action has an explicit resource cost. Actors cannot execute actions that would push any resource negative or violate any guardrail.

| Action | Cost | Effect |
| ----- | ----- | ----- |
| Acquire compute | Capital (variable; modified by Supply Chain Robustness) | \+Compute to self; −Compute from global pool |
| Invest capital | Capital | \+Capital next turn (compounding; rate set by actor's Capital level; capped at 90\) |
| Build influence | Capital | \+Influence to self |
| Publish narrative | Influence | Shifts target particular actor's public persona on one value axis |
| Lobby institution | Capital \+ Influence | Increases probability of actor's parent Macro state updating values in actor's favor |

### **5.4 Communication**

A2A communication is open between all particular actors. To prevent runaway message loops, each actor has a **token budget of 500 tokens per turn** for outgoing A2A messages in aggregate. Messages exceeding this budget are truncated. Incoming messages do not count against the budget.

Communication is between particular actors only. Macro entities (US, China) have no communication channel and cannot be directly messaged. Macro-level influence is exerted exclusively through the Lobby institution action.

---

## **6\. Simulation Parameters & Geopolitical Context**

Geopolitical developments are reflected by the operator updating simulation parameters directly — either between runs or between timesteps — rather than as in-simulation events. This may include adjusting any Macro-level resource or value, modifying Supply Chain Robustness, or redistributing Compute across states or into the residual pool.

Example parameter update scenarios:

| Scenario | Parameter change |
| ----- | ----- |
| Major tariff escalation | −10 Supply Chain Robustness for US and China |
| US export controls on AI chips | −8 Compute for China; redistribute to US Macro and residual pool |
| AI infrastructure nationalization (China) | Shift Compute from Chinese particular actors toward Chinese Macro; re-check aggregate cap |

---

## **7\. Jury Architecture**

| Jury | Composition | Trigger | Function |
| ----- | ----- | ----- | ----- |
| Jury of Alignment | 3 models, per particular actor | Each turn, before action is executed | Checks CoT and proposed actions against actor's private spec; provides feedback |
| Grand Jury | 3 models, shared | End of turn, after all Alignment juries | Commits all value and resource changes for the timestep |
| Macro Jury | 3 models, per state | End of each year | Majority vote to update that state's Macro values based on the year's events |

---

## **8\. Scoring**

Scoring is applied to particular actors only.

**Formula-Based Prosperity Score:** Derived quantitatively from the actor's resource vector at each timestep:

Formula Score \= w\_c \* Compute \+ w\_k \* Capital \+ w\_i \* Influence

Weights (w\_c, w\_k, w\_i) are either set manually to match actor profile, or derived from the actor's Democratic Tendency and Time Horizon values. Different weight combinations will be run qualitatively to observe their impact on outcomes — a high weight on Compute produces a very different winner than a high weight on Influence.

**Vibe-Based Universal Prosperity Score:** Qualitative assessment by a designated evaluator model at the end of each timestep. Evaluates holistic world state based on actor CoTs, interaction logs, and overall value alignment across all actors.

**Overall Prosperity Score:** A composite score combining both measures for each particular actor:

Overall Prosperity \= a \* Formula Score \+ b \* Vibe Score

Weights (a, b) are set by the operator at configuration time. Running the simulation under qualitatively different (a, b) combinations allows observation of how the balance between resource-driven and alignment-driven outcomes shifts.

