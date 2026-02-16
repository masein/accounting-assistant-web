import { Container } from "@cloudflare/containers";

export class AccountingContainer extends Container {
  defaultPort = 8000;
  sleepAfter = "15m";
  enableInternet = true;
  envVars = {
    APP_ENV: "production",
    APP_CORS_ORIGINS: "*",
    DATABASE_URL: "sqlite:////tmp/accounting.db",
    LM_STUDIO_BASE_URL: "",
    LM_STUDIO_MODEL: "",
  };
}

export default {
  async fetch(request, env) {
    const container = env.ACCOUNTING_CONTAINER.getByName("prod");
    await container.startAndWaitForPorts();
    return container.fetch(request);
  },
};
