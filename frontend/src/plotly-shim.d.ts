// plotly.js-dist-min ships a prebuilt bundle without TypeScript types.
declare module "plotly.js-dist-min" {
  const Plotly: {
    react: (
      el: HTMLElement,
      data: unknown[],
      layout?: Record<string, unknown>,
      config?: Record<string, unknown>,
    ) => Promise<void>;
    purge: (el: HTMLElement) => void;
  };
  export default Plotly;
}
