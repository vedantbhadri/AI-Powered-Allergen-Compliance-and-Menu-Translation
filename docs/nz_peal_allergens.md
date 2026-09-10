# NZ MPI / FSANZ 1.2.3 — Mandatory Declarable Allergen (PEAL) Rules

> **Demo knowledge base — starting point for the compliance-verification
> workstream (Bedrock RAG + NZ PEAL rules engine).** Summarised from the NZ
> MPI "Allergen declarations, warnings and advisory statements on food labels"
> page and FSANZ Standard 1.2.3. **Must be reviewed by a food-safety qualified
> person before being relied on in a real commercial setting.** Each `##`
> heading below is a PEAL category name used by the rules engine
> (`services/allergen_rules.py`).

## Gluten (Cereals)

Gluten derived from wheat, rye, barley, oats, spelt, triticale and their
hybrids must always be declared. Common ingredient sources: wheat, flour,
bread, breadcrumbs, batter, pasta, noodles, couscous, barley, rye, oats, oat,
spelt, malt, triticale. Note that most soy sauce also contains wheat. Products
labelled "gluten-free" must contain no gluten, rye, barley, oats or their
hybrids.

## Crustacea

Crustacea must always be declared wherever they appear, including in stocks,
bisques, sauces and garnishes. Common sources: shrimp, prawns, crab, lobster,
crayfish, langoustine. Crustacea can appear in chowders, paella, curries and
Asian stir-fries.

## Molluscs

Molluscs must always be declared wherever they appear. Common sources: clams,
mussels, oysters, scallops, squid, octopus, calamari, snails. Molluscs are
common in seafood chowders and "seafood mix" dishes alongside fish and
crustacea.

## Egg

Egg and egg-derived ingredients must always be declared. Common sources: egg,
mayonnaise, meringue, aioli. Check dressings, sauces, glazes, custards and
baked goods for egg content.

## Fish

Fish and fish-derived ingredients must always be declared. Common sources:
fish, salmon, tuna, anchovy, anchovies, cod, snapper. Fish sauce and
Worcestershire sauce (made with anchovies) contain fish. "Chowder" and "seafood"
descriptions frequently include fish.

## Milk

Milk and milk-derived ingredients must always be declared. Common sources:
milk, cream, butter, cheese, parmesan, mozzarella, yoghurt, yogurt, custard,
ghee, gelato, mascarpone. Hidden milk appears in sauces, dressings, mashed
potatoes, and baked goods.

## Peanuts

Peanuts (also called groundnuts) and peanut-derived ingredients must always be
declared. Common sources: peanut, groundnut, peanut sauce, satay sauce. Peanut
oil and peanut butter also require declaration.

## Soybeans

Soy and soy-derived ingredients must always be declared. Common sources: soy,
soya, soy sauce, tofu, edamame, miso, tempeh. Soy lecithin and soybean oil used
as an ingredient also require declaration.

## Tree Nuts

Each individual tree nut must be declared separately (the category alone is not
sufficient). Individual nuts: almonds, cashews, walnuts, hazelnuts, pistachios,
pecans, macadamias, Brazil nuts, pine nuts. Common hidden sources: praline,
nutella, marzipan, nut-based pestos and sauces.

## Sesame

Sesame and sesame-derived ingredients must always be declared. Common sources:
sesame, sesame seeds, sesame oil, tahini. Sesame appears in hummus, tahini
dressings, crackers and Asian dishes.

## Lupin

Lupin and lupin-derived ingredients must always be declared. Common sources:
lupin, lupini beans, lupin flour. Lupin flour may be used in bread and pasta.

## Added Sulphites

Added sulphites (also spelled sulfites) only need to be declared when present
at or above 10 mg/kg. Common sources: dried fruit (for example dried apricots),
dried fruit mixes, wine reductions, pickled products and some dressings. If the
added concentration is unknown, treat a sulphite keyword hit as "likely" and
flag it for human review rather than confirming.
