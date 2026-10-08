# Location as Code Specification — Draft 1

LocationScope supports canonical country, state/province, county, city, postal geography, point/bounds/polygon identifiers, unions, intersections, exclusions, named sets and weighted distributions. Human strings are resolution input, never execution identity. Resolution produces canonical IDs plus reference-asset provenance. Ambiguity MUST fail closed. Address generation MUST preserve coherent country↔state↔county↔city↔postal↔street↔lat/lon↔timezone relationships.
