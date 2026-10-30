# DataPacket.py

from typing import Optional, Callable, Dict


class DataPacket:
    """
    A structured data class to pass information between application components.
    """

    def __init__(
        self,
        ans: str = "No answer provided.",
        image_path: str = "",
        original_timestamp_str: str = "",
        model_name: str = "Unknown Model",
        prompt_name: str = "Unknown Prompt",
        rerun_prompt_callback: Optional[Callable] = None,
        create_new: bool = False,
        api_key_index: str = "?",
        api_key_mask: str = "????",
        record_audio_callback: Optional[Callable] = None,
        transcribe_callback: Optional[Callable] = None,
        select_audio_files_callback: Optional[Callable] = None,
        get_audio_duration_callback: Optional[Callable] = None,
        pause_audio_callback: Optional[Callable] = None,
        gemini_instance=None,
        skip_history: bool = False,
        toggle_follow_callback: Optional[Callable] = None,
        get_follow_state_callback: Optional[Callable] = None,
        get_audio_stt_state_callback: Optional[Callable] = None,
    ):
        self.ans: str = ans
        self.image_path: str = image_path
        self.original_timestamp_str: str = original_timestamp_str
        self.model_name: str = model_name
        self.prompt_name: str = prompt_name
        self.rerun_prompt_callback: Optional[Callable] = rerun_prompt_callback
        self.create_new: bool = create_new
        self.api_key_index: str = api_key_index
        self.api_key_mask: str = api_key_mask
        self.record_audio_callback: Optional[Callable] = record_audio_callback
        self.transcribe_callback: Optional[Callable] = transcribe_callback
        self.select_audio_files_callback: Optional[Callable] = select_audio_files_callback
        self.get_audio_duration_callback: Optional[Callable] = (
            get_audio_duration_callback
        )
        self.pause_audio_callback: Optional[Callable] = pause_audio_callback
        self.gemini_instance = gemini_instance
        self.skip_history: bool = skip_history
        self.toggle_follow_callback: Optional[Callable] = toggle_follow_callback
        self.get_follow_state_callback: Optional[Callable] = get_follow_state_callback
        self.get_audio_stt_state_callback: Optional[Callable] = (
            get_audio_stt_state_callback
        )

    def __repr__(self) -> str:
        """Provides a developer-friendly representation of the object."""
        return (
            "DataPacket(ans='" + self.ans[:20] + "...', "
            f"image='{self.image_path.split('/')[-1]}', "
            f"model='{self.model_name}', "
            f"prompt='{self.prompt_name}')"
        )

    # --- HÀM ĐÃ THAY ĐỔI ---
    @staticmethod
    def from_gemini_result(
        result: Optional[str] | Dict,
        timestamp_str: str,
        save_path: str,
        is_comparison_rerun: bool,
        force_new_window: bool,
        # Nhận các thông tin cần thiết trực tiếp
        model_name: str,
        prompt_name: str,
        rerun_callback_provider: Callable,
        record_audio_callback_provider: Optional[Callable] = None,
        transcribe_callback_provider: Optional[Callable] = None,
        select_audio_files_callback_provider: Optional[Callable] = None,
        get_audio_duration_callback_provider: Optional[Callable] = None,
        pause_audio_callback_provider: Optional[Callable] = None,
        gemini_instance=None,
        toggle_follow_callback_provider: Optional[Callable] = None,
        get_follow_state_callback_provider: Optional[Callable] = None,
        get_audio_stt_state_callback_provider: Optional[Callable] = None,
    ) -> "DataPacket":
        """
        A factory method to create a DataPacket instance from Gemini processing results.
        It now receives all necessary data directly to avoid circular dependencies.
        """
        should_create_new = force_new_window or is_comparison_rerun

        # Default values
        ans = ""
        api_key_index = "?"
        api_key_mask = "????"

        if result:
            if isinstance(result, dict):
                raw_ans = result.get("text", "")
                api_key_index = str(result.get("api_index", "?"))
                api_key_mask = str(result.get("key_mask", "????"))
            else:
                raw_ans = str(result)

            import re

            cleaned_result = re.sub(re.compile(r"<[/]?u>|<[/]?b>"), "", raw_ans)
            ans = cleaned_result
        else:
            ans = f"Error processing (TS: {timestamp_str}).\nDouble-click to retry."

        # Tạo và trả về instance
        return DataPacket(
            ans=ans,
            image_path=save_path,
            original_timestamp_str=timestamp_str,
            model_name=model_name,
            prompt_name=prompt_name,
            rerun_prompt_callback=rerun_callback_provider,
            create_new=should_create_new,
            api_key_index=api_key_index,
            api_key_mask=api_key_mask,
            record_audio_callback=record_audio_callback_provider,
            transcribe_callback=transcribe_callback_provider,
            select_audio_files_callback=select_audio_files_callback_provider,
            get_audio_duration_callback=get_audio_duration_callback_provider,
            pause_audio_callback=pause_audio_callback_provider,
            gemini_instance=gemini_instance,
            toggle_follow_callback=toggle_follow_callback_provider,
            get_follow_state_callback=get_follow_state_callback_provider,
            get_audio_stt_state_callback=get_audio_stt_state_callback_provider,
        )
