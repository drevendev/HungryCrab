# B2 judging rubric, revision 1

Pass 1 receives only the frozen maw and the shuffled value batch. For every opaque id, return
useful (boolean), garbage (boolean), quality (integer 0..3), and a reason. Exactly one of useful
and garbage is true. Useful means a maintainer would act on a concrete, relevant missing
capability. Garbage reasons include duplicate, wrong stack, already present, and vague advice.
Quality anchors: 0 generic; 1 maw-specific but no concrete step; 2 concrete step without evidence;
3 concrete step naming real maw files/tools with supporting evidence. Relative citations are
visible; source contents and URLs are withheld. Pass 2 verifies the citations. Do not infer an
arm or model from writing style. Use the same anchors on every card.

Pass 2 receives the facts batch and frozen prey. Return evidence_ok and license_ok (booleans)
for every opaque id. Verify that cited files and lines at that commit support the claim, not
merely that the paths exist. Verify external provider references manually. Check file exceptions
and commenter origin, not only the root license. An empty evidence list is not proof.

Judge the value batch twice. A human ChatGPT audit judges the frozen 20% sample using the same
anchors. The scored judge is authoritative; audit disagreement above 15% marks the rubric
suspect and prohibits a comparative conclusion. Report agreement and repeat spread. Never
invent missing runs, usage, costs or judgments. A single run cannot support a model comparison.
