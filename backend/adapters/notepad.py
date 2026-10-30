from utils.helpers import SafeError


class NotepadAdapter:
    @staticmethod
    def require_document(target):
        if target.app != 'notepad' or not target.runtime_id:
            raise SafeError('Extracted data can only be written to a verified Notepad document')
