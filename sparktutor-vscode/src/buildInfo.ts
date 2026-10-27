/** Embedded by esbuild, so an already-loaded extension reports its own build. */
declare const __SPARKTUTOR_BUILD__: string;
export const extensionBuild = typeof __SPARKTUTOR_BUILD__ === "string" ? __SPARKTUTOR_BUILD__ : "未打包的开发源码";
