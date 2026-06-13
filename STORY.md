<!-- LANG-SWITCH -->
**English** | [中文](./STORY.zh.md) | [日本語](./STORY.ja.md)

# Symbol Aligner — The Story

## How this project came about

During the refactoring of a legacy project, we ran into an awkward situation: the
refactor came together with a change to the business naming conventions, so a large
amount of code had to be renamed. Because the project was full of non-standard names,
a blind project-wide find-and-replace would cause many wrong or missed replacements,
while doing it by hand was not only extremely time-consuming but also impossible to
keep at a consistent quality.

Let me first explain why this problem is hard, and why it matters.

Renaming refactors have the following characteristics:

1. The text is overwhelmingly made of compound words, usually composed from a few
   simple text mappings (hereafter called *sub-mappings*).
2. A sub-mapping does not apply to every compound word. The compound mapping formed by
   two sub-mappings is **not** a simple concatenation — one of the sub-mappings may be
   invalidated, with no discernible rule (this depends on how the business was designed,
   not on the engineering team's intent).
3. Because of bad habits from early developers, the codebase contains non-standard names
   that are nonetheless highly similar to the standard ones — e.g. dropped vowels or
   spelling mistakes.

On top of this, the codebase suffered from a chaotic architecture and tight coupling
between modules, which made renaming much harder: once a variable's scope crossed file
boundaries, an unreliable rename would introduce more compile errors across the project.
Inconsistent renames across files also break index construction — the LSP tends to treat
the pre- and post-rename variables as separate entities, destroying contextual integrity.

For these reasons, before undertaking large-scale code conversion, it was essential to
first find a **highly reliable** method for the renaming problem; otherwise the negative
impact on the downstream work would be impossible to estimate.

## The team's earlier attempts

In the early phase of the project, team members proposed many solutions, but practice
showed they were either inefficient or unable to meet the accuracy requirements. Before
introducing this repository's design, it is worth reviewing the attempts I consider
highly inefficient, and analyzing where they went wrong.

### Attempt #1

The earliest attempt predated my joining the project.

It was based on simple mapping relations plus a blacklist mechanism: it enumerated all
possible sub-mapping entries, as well as the entries that must **not** be converted even
when a sub-mapping exists, and fed these as part of the prompt to an Agent. The Agent
handled a single file per isolated session. The approach came with several prompt files
and was split into two steps:

1. A human supplied the prepared prompt files and the target file; the Agent searched and
   analyzed the file per the instructions, found every location needing a rename, and
   organized them into a Markdown table in a predefined format. For low-confidence
   mappings, a human had to consult the single source of truth — a shared spreadsheet —
   and reply to the Agent in a predefined way.
2. Once the mappings were clarified through the replies in step one, another prepared
   prompt was provided so the Agent would make the edits following a predefined workflow
   and produce output in a predefined format.

I suspect that reading the above already made you wince — perhaps even left you speechless.

1. Clearly, the entire process was fixed: fixed sub-mappings, a fixed workflow, and a
   fixed output format. Yet none of this was ever distilled into a reusable Skill.
2. The proposers were not unaware of naming characteristic #2 — that a compound mapping
   is not a simple stack of sub-mappings — which is why they added a human-review step.
   But they still chose to temporarily ignore the problem, converting technical debt into
   human labor cost.
3. The approach ignored compute cost — not a big deal under GitHub Copilot's billing
   before June 2026, but under the current billing, compute consumption is something you
   cannot avoid considering.

The approach failed to recognize that an Agent is not a tool with a stable mapping, but
an intelligent artifact built on top of a probabilistic model — which is exactly why it
is called an *AI* Agent. In the end, practice proved this approach a thorough failure, and
the files it produced left serious hidden risks for the later conversion and testing work.

### Attempt #2

After Attempt #1 fell through, another team proposed a new plan — perhaps influenced by
MAS design and the AI-harness mindset, they built a Skill to handle the renaming problem.
They designed a complex MAS, used an Orchestrator for global scheduling, and defined
multiple roles to carry out a fairly long workflow — at least five stages as I recall,
each possibly with several variants — and managed each stage's output with reports to
improve auditability.

In the end, this approach exhibited several traits:

1. It over-relied on the Agent's reasoning ability, so it could not guarantee the quality
   of the renames.
2. Because the workflow was long, small errors could accumulate into large ones along the
   chain.
3. Because the workflow was long, compute consumption was very high — a budget black hole
   under the current GitHub Copilot billing.
4. Because the workflow was long, even though every stage emitted a report, that did not
   mean problems could be quickly localized, so the reports did little to help iterate on
   the process itself.
5. Because the workflow was long, it took too long to run and was inefficient.

In summary, the two attempts are linked and share one core idea — hand the work straight
to the Agent; it can do anything. Attempt #2 is a big improvement over #1, using MAS to
manage context and focusing on auditability and a controllable, reusable process design.
Yet it made a very basic mistake: it ignored the error-amplification effect and produced a
process so complex that even a human struggles to understand it.

## This approach

Personally, I believe that as LLM capabilities have advanced rapidly over the past few
months, many engineers (myself included) have developed an inaccurate sense of the
boundaries of an Agent's abilities. For complex problems, an Agent may propose all sorts
of solutions, but no one guarantees whether those solutions are reliable, efficient, or
even feasible. So when building such applications, keeping in mind that **AI is not
reliable** is a fundamental orientation.

Moreover, many people seem to have access only to high-level Agents while being
unfamiliar with the underlying LLM. For some simple problems, deploying a 7B-parameter
model on CPU may be enough to solve the crux, and everything else can be written as
scripts — yet many people can only think of using an Agent to handle the entire complex
problem. Their effort was misdirected from the very start.

For the main problems present in this project, here is how they are handled:

| Problem | Solution |
| --- | --- |
| Non-linear stacking of sub-mappings | Compound mappings are a finite set; it is necessary to maintain the complete mapping set |
| Non-standard naming | A weighted blend of multiple fuzzy-matching algorithms, covering many kinds of error |
| Reducing human review | Use the LLM only to judge match results, and only within the mapping set |
| Keywords may be embedded in different identifiers | Parse with an AST; match different identifier kinds in different ways |

For the other shortcomings of the two attempts above, this method addresses them as
follows:

1. **Reusability:** the method is implemented as scripts and exposed as an MCP tool.
2. **Compute cost:** the method uses an LLM only; in theory the cost is a few-dozenth (or
   even a hundredth) of an Agent's.
3. **Over-reliance on Agent reasoning:** the method is mostly traditional matching
   algorithms; the LLM is used only for semantic recall during fuzzy matching.
4. **Auditability:** renames are driven by scripts and the AST, so preparing the audit
   data is simple and reliable.

The detailed design is described in the [README](./README.md).
