import { userParserExportUrl, type UserParserExportFormat } from '@/entities/user-parser';

// Скачивание — ссылка с `download`: сервер отдаёт файл, большая база не собирается в
// памяти окна, а ответ-ошибка (база уже удалена, сессия истекла) сохраняется файлом,
// а не уводит приложение со страницы, как сделал бы переход по ссылке.
export function downloadExport(runId: string, format: UserParserExportFormat) {
  const link = document.createElement('a');
  link.href = userParserExportUrl(runId, format);
  link.download = '';
  link.click();
}
