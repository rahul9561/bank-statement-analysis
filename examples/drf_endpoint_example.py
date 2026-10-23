"""A minimal custom DRF endpoint built directly on the engine.

Prefer the ready-made ``bank_statement_django`` app (ownership checks, private
storage, retention). Use this pattern when you need a one-off endpoint, e.g. an
"analyse and return JSON only" call inside an existing API.
"""

from rest_framework import serializers, status
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from bank_statement_engine import BankStatementAnalyzer, BankStatementError

analyzer = BankStatementAnalyzer({"limits": {"max_file_size_bytes": 15 * 1024 * 1024, "max_pages": 300}})


class UploadSerializer(serializers.Serializer):
    statement = serializers.FileField()
    password = serializers.CharField(required=False, allow_blank=True, write_only=True)


class QuickStatementSummaryView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser]
    throttle_classes = [UserRateThrottle]

    def post(self, request):
        s = UploadSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        upload = s.validated_data["statement"]
        try:
            result = analyzer.analyze(b"".join(upload.chunks()), password=s.validated_data.get("password") or None)
        except BankStatementError as exc:
            return Response({"success": False, "error": exc.to_dict()}, status=exc.http_status)
        return Response({
            "success": True,
            "report_id": result.report_id,
            "status": result.status,
            "summary": result.summary(),
            "validation": {"reconciled": result.validation.reconciled,
                           "issues": [i.to_dict() for i in result.validation.issues]},
            "monthly": result.to_dict(include_transactions=False)["monthly"],
        }, status=status.HTTP_200_OK)

# urls.py:  path("api/statements/quick-summary/", QuickStatementSummaryView.as_view())
