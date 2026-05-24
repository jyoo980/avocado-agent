# Experiments with Avocado Agent

## Treatments

- [x] [Baseline Avocado Agent](https://github.com/jyoo980/avocado-agent/tree/24e1aacefab0845e79b3c638e428de7463a1e2ae)
  - No mutation testing tool access, this is the baseline.
- [x] [Avocado Agent with Mutation Testing tool access](https://github.com/jyoo980/avocado-agent/tree/148284c6d426909486a962d399ca9e0e92b666ae)
  - Currently the treatment with the highest kill score.
- [x] [Avocado Agent with directive to produce the strongest specification](https://github.com/jyoo980/avocado-agent/tree/9497e441ff5e8bec74c7506d9bebae878a94a931)
  - Treatment with the second-highest kill score.
- [ ] Avocado Agent with access to the mutation testing tool *and* directive to produce the strongest specification.
- [ ] Avocado Agent without *any* tools (e.g., no access to any of the scripts under [`tools`](./../tools).)
