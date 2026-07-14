from abc import ABC, abstractmethod


class NotificationChannel(ABC):
    @abstractmethod
    def send_job_failure(
        self,
        job_id: str,
        job_name: str,
        error: str,
        tb: str,
        retry_count: int,
    ) -> None:
        """Called when a scheduled job fails (all retries may still be pending)."""
        ...

    @abstractmethod
    def send_connection_accepted(
        self,
        contact_name: str,
        profile_url: str,
        company_name: str,
    ) -> None:
        """Called when a LinkedIn connection request is accepted."""
        ...

    @abstractmethod
    def send_reply_received(
        self,
        contact_name: str,
        company_name: str,
        reply_snippet: str,
    ) -> None:
        """Called when a reply to a LinkedIn pitch DM is detected."""
        ...
