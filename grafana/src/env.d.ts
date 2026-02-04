declare namespace NodeJS {
  interface ProcessEnv {
    VM_DS_UID?: string;
    GRAFANA_URL?: string;
    GRAFANA_TOKEN?: string;
    GRAFANA_FOLDER_UID?: string;
    GRAFANA_SKIP_UPLOAD?: string;
  }
}
