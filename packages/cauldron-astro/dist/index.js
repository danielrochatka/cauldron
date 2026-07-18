export function defineCauldronContentSource(root) {
  return { kind: 'site-content', root };
}

export function cauldronAstro(options = {}) {
  return {
    name: '@procyonsoft/cauldron-astro',
    hooks: {
      'astro:config:setup': ({ logger }) => {
        logger.info(
          `Cauldron Astro integration enabled with content root: ${options.contentRoot ?? 'site-owned content'}`,
        );
      },
    },
  };
}
