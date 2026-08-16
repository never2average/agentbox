{{- define "agentbox.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "agentbox.labels" -}}
app.kubernetes.io/name: {{ include "agentbox.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: agentbox
{{- end -}}

{{- define "agentbox.serviceAccountName" -}}
{{- default "agentbox-controller" .Values.serviceAccount.name -}}
{{- end -}}
